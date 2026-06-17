"""Draining detached sub-agent workers on stop, and reaping loop-less orphans.

These use real spawned processes (session leaders and a non-leader) so the
process-group kill path and its safety guard are exercised for real, not mocked.

Test note: pytest is the parent of the spawned sleepers, so a killed child would
linger as a zombie (``os.kill(pid, 0)`` still succeeds) and keep its process
group "present". In production the drainer is a separate process and init reaps
the orphans immediately; here a per-process background ``wait()`` thread plays
init's role so the group disappears as it would in the field.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest
from typer.testing import CliRunner

from iteris.agents.runtime import (
    drain_agent_run,
    drain_project_workers,
    pid_running,
    terminate_pgroup,
)
from iteris.cli import app
from iteris.liveness import scan_agent_runs
from iteris.project import init_project, now_iso, read_json, write_json

pytestmark = pytest.mark.skipif(os.name != "posix", reason="process-group draining is posix-only")

runner = CliRunner()
SLEEP = "import time; time.sleep(120)"


def _reap_async(proc: subprocess.Popen) -> None:
    threading.Thread(target=lambda: _safe_wait(proc), daemon=True).start()


def _safe_wait(proc: subprocess.Popen) -> None:
    try:
        proc.wait()
    except Exception:
        pass


def _spawn_leader() -> subprocess.Popen:
    """A child in its own session (pgid == pid, like a real worker), auto-reaped."""
    proc = subprocess.Popen([sys.executable, "-c", SLEEP], start_new_session=True)
    _reap_async(proc)
    return proc


def _spawn_nonleader() -> subprocess.Popen:
    """A child sharing the test runner's group: getpgid(pid) != pid."""
    proc = subprocess.Popen([sys.executable, "-c", SLEEP])
    _reap_async(proc)
    return proc


def _pid_dead(pid: int, timeout: float = 6.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not pid_running(pid):
            return True
        time.sleep(0.05)
    return not pid_running(pid)


def _kill(*procs: subprocess.Popen) -> None:
    for proc in procs:
        if proc.poll() is None:
            proc.kill()


def _make_run(project: Path, run_id: str, status: dict) -> Path:
    run_dir = project / "artifacts" / "agent_runs" / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    write_json(run_dir / "request.json", {"run_id": run_id, "role": "execute", "project_path": str(project)})
    write_json(run_dir / "status.json", {"schema_version": "iteris.agent_run_status.v0", **status})
    return run_dir


def _make_project(tmp_path: Path) -> Path:
    source = tmp_path / "problem.tex"
    source.write_text("\\begin{problem}Test.\\end{problem}", encoding="utf-8")
    project = tmp_path / "project"
    project.mkdir()
    init_project(project, source=source)
    return project


def test_terminate_pgroup_reaps_the_whole_subtree():
    # A leader that spawns a child in its own group; killing the group kills both.
    code = (
        "import subprocess, sys, time;"
        "c = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(120)']);"
        "print(c.pid, flush=True); time.sleep(120)"
    )
    proc = subprocess.Popen([sys.executable, "-c", code], stdout=subprocess.PIPE, text=True, start_new_session=True)
    _reap_async(proc)
    try:
        child_pid = int((proc.stdout.readline() or "0").strip())
        assert child_pid > 1
        assert pid_running(proc.pid) and pid_running(child_pid)
        assert terminate_pgroup(proc.pid, force_after=3.0) in {"TERM", "KILL"}
        assert _pid_dead(proc.pid)
        assert _pid_dead(child_pid)  # the descendant was reaped too
    finally:
        _kill(proc)


def test_terminate_pgroup_on_dead_group_is_idempotent():
    proc = _spawn_leader()
    proc.kill()
    assert _pid_dead(proc.pid)
    assert terminate_pgroup(proc.pid, force_after=1.0) == "already_exited"


def test_drain_agent_run_kills_worker_and_codex_groups(tmp_path):
    worker = _spawn_leader()
    codex = _spawn_leader()
    run_dir = _make_run(tmp_path, "execute-x", {"status": "running", "pid": worker.pid, "codex_pgid": codex.pid, "updated_at": now_iso()})
    try:
        out = drain_agent_run(tmp_path, "execute-x", force_after=3.0)
        assert out["actions"]["worker"]["result"] in {"TERM", "KILL"}
        # Legacy ``codex_pgid`` status field is still honored (fallback read);
        # the reap action is now reported under the executor-agnostic key.
        assert out["actions"]["executor"]["result"] in {"TERM", "KILL"}
        assert _pid_dead(worker.pid)
        assert _pid_dead(codex.pid)
        assert read_json(run_dir / "status.json")["status"] == "failed"
    finally:
        _kill(worker, codex)


def test_drain_refuses_to_signal_a_non_session_leader(tmp_path):
    # Safety guard: a recorded pid that is NOT a dedicated session leader must
    # never be signaled (it could be a recycled pid or an unrelated process).
    child = _spawn_nonleader()
    _make_run(tmp_path, "execute-safe", {"status": "running", "pid": child.pid, "updated_at": now_iso()})
    try:
        out = drain_agent_run(tmp_path, "execute-safe", force_after=1.0)
        assert out["actions"]["worker"]["result"] == "skipped_not_session_leader"
        assert pid_running(child.pid)  # left untouched
    finally:
        _kill(child)


def test_drain_project_workers_drains_live_workers(tmp_path):
    project = _make_project(tmp_path)
    worker = _spawn_leader()
    _make_run(project, "execute-w", {"status": "running", "pid": worker.pid, "updated_at": now_iso()})
    try:
        out = drain_project_workers(project, force_after=3.0)
        assert out["count"] == 1
        assert _pid_dead(worker.pid)
    finally:
        _kill(worker)


def test_scan_marks_live_worker_orphaned_only_when_loop_gone(tmp_path):
    project = _make_project(tmp_path)
    worker = _spawn_leader()
    _make_run(project, "execute-y", {"status": "running", "pid": worker.pid, "updated_at": now_iso()})
    try:
        # session alive -> live; session gone -> orphaned with pid_alive flag
        assert any(e["run_id"] == "execute-y" for e in scan_agent_runs(project, session_live=True)["live"])
        orphaned = scan_agent_runs(project, session_live=False)["orphaned"]
        entry = next(e for e in orphaned if e["run_id"] == "execute-y")
        assert entry["pid_alive"] is True and entry["reason"] == "owning_loop_gone"
    finally:
        _kill(worker)


def test_recover_reaps_orphaned_live_worker_when_loop_dead(tmp_path):
    project = _make_project(tmp_path)
    worker = _spawn_leader()
    codex = _spawn_leader()
    run_dir = _make_run(project, "execute-orphan", {"status": "running", "pid": worker.pid, "codex_pgid": codex.pid, "updated_at": now_iso()})
    try:
        # No tmux session exists for this temp project -> the loop is "dead".
        result = runner.invoke(app, ["recover", str(project), "--json"])
        assert result.exit_code == 0, result.output
        payload = json.loads(result.output)
        assert any(item["action"] == "reap_orphaned_worker" for item in payload["actions"])
        assert _pid_dead(worker.pid)
        assert _pid_dead(codex.pid)
        assert read_json(run_dir / "status.json")["status"] == "failed"
    finally:
        _kill(worker, codex)

"""Runtime support for background Codex subagents."""

from __future__ import annotations

import json
import os
import shlex
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Callable

from iteris.artifacts import create_artifact_workspace, update_manifest_from_agent_output
from iteris.codex_logs import (
    CODEX_EVENTS_FILENAME,
    CODEX_LOG_MANIFEST_FILENAME,
    CODEX_STDERR_FILENAME,
    ensure_codex_exec_json,
    render_codex_events,
    run_codex_exec_json,
)
from iteris.events import record_event
from iteris.executors import (
    EXECUTOR_CLAUDE,
    build_claude_headless_command,
    build_codex_headless_command,
    headless_home_env,
    resolve_agent_model,
    resolve_executor,
)
from iteris.log_adapters import render_claude_events
from iteris.project import now_iso, now_stamp, read_json, slugify, write_json


DEFAULT_MODEL = os.getenv("ITERIS_AGENT_MODEL", os.getenv("CODEX_MODEL", "gpt-5.5"))
DEFAULT_REASONING_EFFORT = os.getenv("ITERIS_AGENT_REASONING_EFFORT", "high")
DEFAULT_TIMEOUT_SECONDS = int(os.getenv("ITERIS_AGENT_TIMEOUT_SECONDS", "7200"))


PromptBuilder = Callable[[dict[str, Any]], str]


def iteris_cli_path() -> str:
    """Absolute path to the ``iteris`` console script, or the bare name.

    Codex subagents run tool commands through ``bash -lc`` (a login shell that
    sources /etc/profile and RESETS PATH), so a bare ``iteris`` may not be on
    PATH and ``iteris tool ...`` calls fail. The console script lives next to
    the interpreter that launched us; hand the worker that absolute path as a
    fallback. Fall back to the bare name if the script is not co-located (e.g.
    an editable install exposing ``iteris`` elsewhere on PATH).
    """
    candidate = Path(sys.executable).parent / "iteris"
    return str(candidate) if candidate.exists() else "iteris"


def build_codex_command(
    *,
    project_root: Path,
    executable: str,
    model: str,
    reasoning_effort: str,
) -> list[str]:
    # Back-compat shim; the canonical builder now lives in iteris.executors.
    return build_codex_headless_command(
        project_root=project_root,
        executable=executable,
        model=model,
        reasoning_effort=reasoning_effort,
    )


def create_agent_run(
    project_root: Path,
    *,
    role: str,
    prompt_builder: PromptBuilder,
    mode: str | None = None,
    task_id: str | None = None,
    focus: str | None = None,
    detached: bool = False,
    dry_run: bool = False,
    executor: str | None = None,
    executable: str | None = None,
    model: str | None = None,
    reasoning_effort: str | None = None,
    timeout_seconds: int | None = None,
) -> dict[str, Any]:
    root = project_root.resolve()
    # Sub-agents default to the same executor as the running /goal loop, which
    # exports ITERIS_EXECUTOR into its subtree; an explicit value overrides.
    executor_name = resolve_executor(executor)
    safe_label = slugify(task_id or focus or mode or role, 44)
    run_id = f"{role}-{now_stamp()}-{safe_label}"
    run_dir = root / "artifacts" / "agent_runs" / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    artifact_request = create_artifact_workspace(
        root,
        run_id=run_id,
        role=role,
        mode=mode,
        task_id=task_id,
        focus=focus,
        agent_run_dir=run_dir,
    )

    if executor_name == EXECUTOR_CLAUDE:
        agent_bin = executable or shutil.which("claude") or "claude"
        agent_command = build_claude_headless_command(
            project_root=root,
            executable=agent_bin,
            model=resolve_agent_model(executor_name, model, kind="agent"),
        )
    else:
        agent_bin = executable or shutil.which("codex") or "codex"
        agent_command = build_codex_headless_command(
            project_root=root,
            executable=agent_bin,
            model=model or DEFAULT_MODEL,
            reasoning_effort=reasoning_effort or DEFAULT_REASONING_EFFORT,
        )
    request = {
        "schema_version": "iteris.agent_run_request.v0",
        "run_id": run_id,
        "executor": executor_name,
        "role": role,
        "mode": mode,
        "task_id": task_id,
        "focus": focus,
        "project_path": str(root),
        "iteris_cli": iteris_cli_path(),
        "detached": detached,
        "dry_run": dry_run,
        "created_at": now_iso(),
        "prompt_path": str((run_dir / "prompt.md").relative_to(root)),
        "output_markdown": str((run_dir / "output.md").relative_to(root)),
        "output_json": str((run_dir / "output.json").relative_to(root)),
        "status_path": str((run_dir / "status.json").relative_to(root)),
        "codex_log": str((run_dir / "codex.log").relative_to(root)),
        "codex_events": str((run_dir / CODEX_EVENTS_FILENAME).relative_to(root)),
        "codex_stderr": str((run_dir / CODEX_STDERR_FILENAME).relative_to(root)),
        "codex_log_manifest": str((run_dir / CODEX_LOG_MANIFEST_FILENAME).relative_to(root)),
        **artifact_request,
        "timeout_seconds": timeout_seconds if timeout_seconds is not None else DEFAULT_TIMEOUT_SECONDS,
        # Legacy key name predates multi-executor support; it holds the active
        # executor's headless command regardless of which CLI launched.
        "codex_command": agent_command,
    }
    prompt = prompt_builder(request)
    (run_dir / "prompt.md").write_text(prompt, encoding="utf-8")
    write_json(run_dir / "request.json", request)
    write_status(run_dir, {"status": "dry_run" if dry_run else "pending", "updated_at": now_iso()})
    record_event(root, "agent_run_created", {"run_id": run_id, "role": role, "mode": mode, "task_id": task_id, "detached": detached, "dry_run": dry_run})

    if dry_run:
        return agent_run_summary(root, run_dir)

    if detached:
        worker_log = run_dir / "worker.log"
        worker_cmd = [sys.executable, "-m", "iteris.agents.worker", str(run_dir)]
        with worker_log.open("a", encoding="utf-8") as handle:
            handle.write(f"started_at: {now_iso()}\n")
            handle.write(f"command: {shlex.join(worker_cmd)}\n\n")
            handle.flush()
            proc = subprocess.Popen(
                worker_cmd,
                cwd=root,
                stdout=handle,
                stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL,
                text=True,
                start_new_session=True,
            )
        write_status(
            run_dir,
            {
                "status": "running",
                "pid": proc.pid,
                "worker_command": worker_cmd,
                "worker_log": str(worker_log.relative_to(root)),
                "started_at": now_iso(),
                "updated_at": now_iso(),
            },
        )
        record_event(root, "agent_run_started", {"run_id": run_id, "role": role, "mode": mode, "task_id": task_id, "pid": proc.pid})
        return agent_run_summary(root, run_dir)

    result = run_agent_exec(run_dir)
    record_event(root, "agent_run_completed", {"run_id": run_id, "role": role, "mode": mode, "task_id": task_id, "returncode": result["returncode"]})
    return agent_run_summary(root, run_dir)


def run_agent_exec(run_dir: Path) -> dict[str, Any]:
    run_dir = run_dir.resolve()
    request = read_json(run_dir / "request.json", default={})
    if not isinstance(request, dict):
        raise RuntimeError(f"agent request is missing or invalid: {run_dir / 'request.json'}")
    root = Path(str(request["project_path"])).resolve()
    executor_name = resolve_executor(request.get("executor"))
    prompt = (run_dir / "prompt.md").read_text(encoding="utf-8")
    cmd = [str(item) for item in request.get("codex_command") or []]
    if not cmd:
        raise RuntimeError("agent request has no executor command")
    # No-op for claude (argv[1] is "-p"); only codex grows a "--json" flag here.
    json_cmd = ensure_codex_exec_json(cmd)
    if json_cmd != cmd:
        request["codex_command"] = json_cmd
        request.setdefault("codex_events", str((run_dir / CODEX_EVENTS_FILENAME).relative_to(root)))
        request.setdefault("codex_stderr", str((run_dir / CODEX_STDERR_FILENAME).relative_to(root)))
        request.setdefault("codex_log_manifest", str((run_dir / CODEX_LOG_MANIFEST_FILENAME).relative_to(root)))
        write_json(run_dir / "request.json", request)
        cmd = json_cmd
    if shutil.which(cmd[0]) is None and not Path(cmd[0]).exists():
        write_status(run_dir, {"status": "failed", "error": f"{executor_name} executable is not installed", "updated_at": now_iso()})
        raise RuntimeError(f"{executor_name} executable is not installed; subagent cannot run")

    render_fn = render_claude_events if executor_name == EXECUTOR_CLAUDE else render_codex_events
    started_at = now_iso()
    # Record the worker pid for foreground runs too, so liveness scanning can
    # tell an in-flight foreground run apart from one orphaned by a crash.
    write_status(run_dir, {"status": "running", "pid": os.getpid(), "started_at": started_at, "updated_at": started_at})
    log_manifest = run_codex_exec_json(
        project_root=root,
        run_dir=run_dir,
        process_kind=f"subagent_{request.get('role') or 'agent'}",
        run_id=str(request.get("run_id") or run_dir.name),
        command=cmd,
        prompt=prompt,
        prompt_path=run_dir / "prompt.md",
        executor=executor_name,
        render_fn=render_fn,
        log_adapter=executor_name,
        env_updates={
            "ITERIS_PROCESS_ROLE": f"subagent_{request.get('role') or 'agent'}",
            "ITERIS_AGENT_RUN_ID": str(request.get("run_id") or run_dir.name),
            "ITERIS_PROJECT_ROOT": str(root),
            "ITERIS_EXECUTOR": executor_name,
            **headless_home_env(executor_name),
        },
        timeout_seconds=int(request.get("timeout_seconds") or DEFAULT_TIMEOUT_SECONDS),
        # Persist the executor process group while it runs so `iteris stop` and
        # `iteris recover` can reap the CLI/node subtree (a separate session
        # from this worker) instead of leaving it to burn budget after a stop.
        on_spawn=lambda pid, pgid: write_status(
            run_dir, {"exec_pid": pid, "exec_pgid": pgid, "updated_at": now_iso()}
        ),
    )
    returncode = log_manifest.get("returncode")
    if log_manifest.get("timed_out"):
        output_markdown_exists = (run_dir / "output.md").exists()
        output_json_exists = (run_dir / "output.json").exists()
        if output_markdown_exists and output_json_exists:
            output_payload = read_json(run_dir / "output.json", default=None)
            update_manifest_from_agent_output(root, request, output_payload if isinstance(output_payload, dict) else None, status="completed")
            result = {
                "status": "completed",
                "returncode": None,
                "timed_out": True,
                "completed_at": now_iso(),
                "updated_at": now_iso(),
                "output_markdown_exists": True,
                "output_json_exists": True,
                "warning": "agent process timed out after writing required output files",
            }
            write_status(run_dir, result)
            return result
        result = {"status": "failed", "error": "agent run timed out", "updated_at": now_iso()}
        write_status(run_dir, result)
        return {**result, "returncode": None, "output_markdown_exists": output_markdown_exists, "output_json_exists": output_json_exists}

    output_markdown_exists = (run_dir / "output.md").exists()
    output_json_exists = (run_dir / "output.json").exists()
    missing_outputs = returncode == 0 and not (output_markdown_exists and output_json_exists)
    status = "completed" if returncode == 0 and not missing_outputs else "failed"
    output_payload = read_json(run_dir / "output.json", default=None) if output_json_exists else None
    update_manifest_from_agent_output(root, request, output_payload if isinstance(output_payload, dict) else None, status=status)
    result = {
        "status": status,
        "returncode": returncode,
        "completed_at": now_iso(),
        "updated_at": now_iso(),
        "output_markdown_exists": output_markdown_exists,
        "output_json_exists": output_json_exists,
    }
    if missing_outputs:
        result["error"] = "agent run completed without required output.md and output.json"
    write_status(run_dir, result)
    return result


# Back-compat alias: worker.py and external callers imported the old name.
run_agent_codex = run_agent_exec


def write_status(run_dir: Path, updates: dict[str, Any]) -> dict[str, Any]:
    status_path = run_dir / "status.json"
    current = read_json(status_path, default={})
    if not isinstance(current, dict):
        current = {}
    payload = {**current, **updates}
    payload.setdefault("schema_version", "iteris.agent_run_status.v0")
    write_json(status_path, payload)
    return payload


def agent_run_summary(project_root: Path, run_dir: Path) -> dict[str, Any]:
    root = project_root.resolve()
    request = read_json(run_dir / "request.json", default={})
    status = read_json(run_dir / "status.json", default={})
    if isinstance(status, dict) and status.get("status") == "running" and status.get("pid") and not _pid_running(int(status["pid"])):
        status = write_status(run_dir, {"status": "exited_unknown", "updated_at": now_iso()})
    if isinstance(status, dict) and status.get("status") == "completed" and not ((run_dir / "output.md").exists() and (run_dir / "output.json").exists()):
        status = write_status(
            run_dir,
            {
                "status": "failed",
                "error": "agent run is marked completed but required output.md and output.json are missing",
                "updated_at": now_iso(),
            },
        )
    return {
        "schema_version": "iteris.agent_run_summary.v0",
        "run_id": run_dir.name,
        "executor": request.get("executor") if isinstance(request, dict) else None,
        "role": request.get("role") if isinstance(request, dict) else None,
        "mode": request.get("mode") if isinstance(request, dict) else None,
        "task_id": request.get("task_id") if isinstance(request, dict) else None,
        "focus": request.get("focus") if isinstance(request, dict) else None,
        "status": status.get("status") if isinstance(status, dict) else "unknown",
        "pid": status.get("pid") if isinstance(status, dict) else None,
        "agent_run_dir": str(run_dir.relative_to(root)),
        "prompt_path": _rel(run_dir / "prompt.md", root),
        "request_path": _rel(run_dir / "request.json", root),
        "status_path": _rel(run_dir / "status.json", root),
        "codex_log": _rel(run_dir / "codex.log", root),
        "codex_events": _rel(run_dir / CODEX_EVENTS_FILENAME, root) if (run_dir / CODEX_EVENTS_FILENAME).exists() else request.get("codex_events"),
        "codex_stderr": _rel(run_dir / CODEX_STDERR_FILENAME, root) if (run_dir / CODEX_STDERR_FILENAME).exists() else request.get("codex_stderr"),
        "codex_log_manifest": _rel(run_dir / CODEX_LOG_MANIFEST_FILENAME, root) if (run_dir / CODEX_LOG_MANIFEST_FILENAME).exists() else request.get("codex_log_manifest"),
        "worker_log": status.get("worker_log") if isinstance(status, dict) else None,
        "output_markdown": _rel(run_dir / "output.md", root) if (run_dir / "output.md").exists() else None,
        "output_json": _rel(run_dir / "output.json", root) if (run_dir / "output.json").exists() else None,
        "artifact_workspace": request.get("artifact_workspace") if isinstance(request, dict) else None,
        "artifact_manifest": request.get("artifact_manifest") if isinstance(request, dict) else None,
        "created_at": request.get("created_at") if isinstance(request, dict) else None,
        "updated_at": status.get("updated_at") if isinstance(status, dict) else None,
    }


def list_agent_runs(project_root: Path, *, limit: int = 20) -> list[dict[str, Any]]:
    root = project_root.resolve()
    runs_dir = root / "artifacts" / "agent_runs"
    if not runs_dir.exists():
        return []
    run_dirs = sorted([path for path in runs_dir.iterdir() if path.is_dir()], key=lambda path: path.stat().st_mtime, reverse=True)
    return [agent_run_summary(root, run_dir) for run_dir in run_dirs[:limit]]


def latest_agent_run(project_root: Path) -> Path | None:
    root = project_root.resolve()
    runs_dir = root / "artifacts" / "agent_runs"
    if not runs_dir.exists():
        return None
    run_dirs = sorted([path for path in runs_dir.iterdir() if path.is_dir()], key=lambda path: path.stat().st_mtime, reverse=True)
    return run_dirs[0] if run_dirs else None


def tail_text(path: Path, *, lines: int = 80) -> str:
    if not path.exists():
        return ""
    data = path.read_text(encoding="utf-8", errors="replace").splitlines()
    return "\n".join(data[-lines:])


def pid_running(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


# Backward-compatible alias for callers of the original private name.
_pid_running = pid_running


def terminate_pgroup(pgid: int, *, force_after: float = 5.0, poll_interval: float = 0.25) -> str:
    """SIGTERM, then SIGKILL after a grace period, a whole process group.

    Workers and their codex children are each spawned with
    ``start_new_session=True``, so each is its own group leader; killing the
    group reaps the descendant tree (e.g. the codex ``node`` subtree). Returns
    the strongest action taken: ``TERM``, ``KILL``, ``already_exited``,
    ``permission_denied``, or ``skipped``.
    """
    if not isinstance(pgid, int) or pgid <= 1:
        return "skipped"
    try:
        os.killpg(pgid, signal.SIGTERM)
    except ProcessLookupError:
        return "already_exited"
    except PermissionError:
        return "permission_denied"
    deadline = time.monotonic() + max(force_after, 0.0)
    while time.monotonic() < deadline:
        try:
            os.killpg(pgid, 0)
        except ProcessLookupError:
            return "TERM"
        except PermissionError:
            # The pid was recycled to a group we no longer own between TERM and
            # this probe: stop — never escalate SIGKILL onto an unrelated group.
            return "permission_denied"
        time.sleep(min(poll_interval, max(deadline - time.monotonic(), 0.0)))
    try:
        os.killpg(pgid, signal.SIGKILL)
        return "KILL"
    except ProcessLookupError:
        return "TERM"
    except PermissionError:
        return "permission_denied"


def _reap_session_leader(pid: int, *, force_after: float = 5.0) -> str:
    """Kill the process group led by ``pid`` — but ONLY if ``pid`` is genuinely a
    session/group leader (``getpgid(pid) == pid``).

    Detached workers and codex children are always spawned ``start_new_session``,
    so their recorded pids are group leaders and ``killpg(pid)`` reaps exactly
    their subtree. Refusing to act on a non-leader pid is a safety guard: it
    prevents ever signaling an unrelated process group (e.g. a recycled pid or a
    pid that was never a dedicated session).
    """
    if not isinstance(pid, int) or pid <= 1:
        return "skipped"
    try:
        if os.getpgid(pid) != pid:
            return "skipped_not_session_leader"
    except ProcessLookupError:
        return "already_exited"
    except OSError:
        return "skipped"
    return terminate_pgroup(pid, force_after=force_after)


def drain_agent_run(
    project_root: Path,
    run_id: str,
    *,
    force_after: float = 5.0,
    reason: str = "stopped",
) -> dict[str, Any]:
    """Terminate a detached agent run's codex subtree and worker, then mark it failed.

    The codex group is killed first (it is the budget burner), then the worker.
    Both are killed by group leader only (see ``_reap_session_leader``).
    Idempotent: groups already gone report ``already_exited``.
    """
    run_dir = (project_root / "artifacts" / "agent_runs" / run_id).resolve()
    status = read_json(run_dir / "status.json", default={})
    status = status if isinstance(status, dict) else {}
    actions: dict[str, Any] = {}
    # Canonical field is exec_pgid; codex_pgid is the pre-multi-executor name,
    # still read so a run launched before the rename is reaped on upgrade.
    exec_pgid = status.get("exec_pgid")
    if not isinstance(exec_pgid, int):
        exec_pgid = status.get("codex_pgid")
    if isinstance(exec_pgid, int):
        actions["executor"] = {"pgid": exec_pgid, "result": _reap_session_leader(exec_pgid, force_after=force_after)}
    worker_pid = status.get("pid")
    if isinstance(worker_pid, int):
        actions["worker"] = {"pid": worker_pid, "result": _reap_session_leader(worker_pid, force_after=force_after)}
    write_status(
        run_dir,
        {"status": "failed", "error": f"{reason}: drained (worker + codex group terminated)", "updated_at": now_iso()},
    )
    return {"run_id": run_id, "actions": actions}


def drain_project_workers(project_root: Path, *, force_after: float = 5.0) -> dict[str, Any]:
    """Drain every live detached worker for a project (used by ``iteris stop``)."""
    from iteris.liveness import scan_agent_runs  # lazy import: liveness imports this module

    root = project_root.resolve()
    live = scan_agent_runs(root)["live"]
    drained = [
        drain_agent_run(root, entry["run_id"], force_after=force_after, reason="stopped")
        for entry in live
        if isinstance(entry.get("pid"), int)
    ]
    return {"drained": drained, "count": len(drained)}


def _rel(path: Path, root: Path) -> str:
    try:
        return str(path.resolve().relative_to(root))
    except ValueError:
        return str(path.resolve())

"""Framework-wide executor switching: headless sub-agents, verifiers, supervision.

The main /goal loop's codex/claude switch is covered by test_executors.py /
test_agent_cli_git.py. These tests cover the rest of the framework now honoring
the same switch: sub-agents, verification agents, the generalization analyzer,
and the supervision judgment backend.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from iteris.executors import (
    build_claude_headless_command,
    build_codex_headless_command,
    headless_home_env,
    resolve_agent_model,
)
from iteris.agents.runtime import create_agent_run, run_agent_exec
from iteris.agents.execute import launch_execute_agent
from iteris.verification.agent import resolve_verification_executor, verify_agent
from iteris.supervision.contracts import _last_agent_message
from iteris.supervision.engine import Profile
from iteris.supervision.contracts import agent_backend
from iteris.project import init_project


# ---------------------------------------------------------------------------
# Unit: headless command builders, model + env resolution.
# ---------------------------------------------------------------------------

def test_codex_headless_command_reads_stdin_with_model_and_effort():
    cmd = build_codex_headless_command(
        project_root=Path("/tmp/p"), executable="codex", model="gpt-5.5", reasoning_effort="high"
    )
    assert cmd[:5] == ["codex", "exec", "--json", "-C", "/tmp/p"]
    assert cmd[-1] == "-"  # prompt on stdin
    assert "--dangerously-bypass-approvals-and-sandbox" in cmd
    assert "model_reasoning_effort=high" in cmd


def test_claude_headless_command_is_print_stream_json():
    cmd = build_claude_headless_command(project_root=Path("/tmp/p"), executable="claude", model="claude-opus-4-8")
    assert cmd[:6] == ["claude", "-p", "--output-format", "stream-json", "--verbose", "--dangerously-skip-permissions"]
    assert cmd[-2:] == ["--model", "claude-opus-4-8"]
    # No --cd / -C: the runner sets cwd. No "-": claude -p reads stdin by default.
    assert "-C" not in cmd and "--cd" not in cmd


def test_claude_headless_command_omits_model_when_absent():
    cmd = build_claude_headless_command(project_root=Path("/tmp/p"))
    assert "--model" not in cmd


def test_headless_home_env_per_executor():
    assert headless_home_env("codex") == {}
    claude_env = headless_home_env("claude")
    assert claude_env["IS_SANDBOX"] == "1"
    assert claude_env["CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC"] == "1"
    # Unlike the main loop, no per-run CLAUDE_CONFIG_DIR (events come from stdout).
    assert "CLAUDE_CONFIG_DIR" not in claude_env


def test_resolve_agent_model_defaults():
    assert resolve_agent_model("codex", None, env={}) == "gpt-5.5"
    assert resolve_agent_model("codex", "explicit", env={}) == "explicit"
    assert resolve_agent_model("codex", None, env={"CODEX_MODEL": "gpt-x"}) == "gpt-x"
    # Claude has no hardcoded default — None lets the claude CLI pick its own.
    assert resolve_agent_model("claude", None, env={}) is None
    assert resolve_agent_model("claude", None, env={"ITERIS_CLAUDE_MODEL": "claude-x"}) == "claude-x"
    assert resolve_agent_model("claude", "explicit", env={}) == "explicit"
    assert resolve_agent_model("claude", None, env={"ITERIS_CLAUDE_VERIFICATION_MODEL": "cv"}, kind="verification") == "cv"


def test_resolve_verification_executor_is_independent():
    # explicit > ITERIS_VERIFICATION_EXECUTOR > ITERIS_EXECUTOR > codex
    assert resolve_verification_executor(None, env={}) == "codex"
    assert resolve_verification_executor(None, env={"ITERIS_EXECUTOR": "claude"}) == "claude"
    assert resolve_verification_executor(None, env={"ITERIS_VERIFICATION_EXECUTOR": "codex", "ITERIS_EXECUTOR": "claude"}) == "codex"
    assert resolve_verification_executor("claude", env={"ITERIS_VERIFICATION_EXECUTOR": "codex"}) == "claude"


# ---------------------------------------------------------------------------
# Sub-agent dispatch.
# ---------------------------------------------------------------------------

def test_create_agent_run_claude_dry_run_records_executor(tmp_path):
    project = tmp_path / "project"
    init_project(project)
    summary = create_agent_run(
        project,
        role="explore",
        focus="probe",
        prompt_builder=lambda request: "do the thing",
        executor="claude",
        dry_run=True,
    )
    assert summary["executor"] == "claude"
    request = json.loads((project / summary["agent_run_dir"] / "request.json").read_text(encoding="utf-8"))
    assert request["executor"] == "claude"
    cmd = request["codex_command"]  # legacy key holds the active executor's command
    assert Path(cmd[0]).name == "claude" and "-p" in cmd and "stream-json" in cmd


def test_create_agent_run_inherits_executor_from_env(tmp_path, monkeypatch):
    project = tmp_path / "project"
    init_project(project)
    monkeypatch.setenv("ITERIS_EXECUTOR", "claude")
    summary = create_agent_run(
        project, role="explore", focus="x", prompt_builder=lambda r: "p", dry_run=True
    )
    assert summary["executor"] == "claude"


def _fake_claude_subagent(path: Path) -> Path:
    """A fake `claude` headless CLI for an execute sub-agent run."""
    path.write_text(
        """#!/bin/sh
python - <<'PY'
import json, os, sys
from pathlib import Path
root = Path(os.environ["ITERIS_PROJECT_ROOT"])
run_id = os.environ["ITERIS_AGENT_RUN_ID"]
assert os.environ["ITERIS_EXECUTOR"] == "claude"
run_dir = root / "artifacts" / "agent_runs" / run_id
request = json.loads((run_dir / "request.json").read_text(encoding="utf-8"))
workspace = root / request["artifact_workspace"]
workspace.mkdir(parents=True, exist_ok=True)
(workspace / "proof.md").write_text("# Proof\\n\\n## Proof\\n\\nchecked.\\n", encoding="utf-8")
(run_dir / "output.md").write_text("# Agent output\\n\\ndone.\\n", encoding="utf-8")
(run_dir / "output.json").write_text(json.dumps({
    "schema_version": "iteris.agent_output.v0",
    "role": "execute", "run_id": run_id, "mode": request["mode"], "task_id": request["task_id"],
    "summary": "claude did it.", "status_recommendation": "done",
    "created_artifacts": [str((workspace / "proof.md").relative_to(root))],
    "artifact_manifest": request["artifact_manifest"], "updated_shared_files": [],
    "candidate_facts": [], "verification_requests": [], "blockers": [],
    "task_pool_updates": [], "next_actions": []
}) + "\\n", encoding="utf-8")
# claude -p --output-format stream-json emits one JSON event per line.
print(json.dumps({"type":"system","subtype":"init","session_id":"claude-sess-1","model":"claude-x"}))
print(json.dumps({"type":"assistant","session_id":"claude-sess-1","message":{"model":"claude-x","content":[{"type":"text","text":"claude agent completed"}]}}))
print(json.dumps({"type":"result","subtype":"success","session_id":"claude-sess-1","result":"done"}))
print("fake claude stderr", file=sys.stderr)
PY
""",
        encoding="utf-8",
    )
    path.chmod(0o755)
    return path


def test_execute_agent_claude_end_to_end(tmp_path):
    project = tmp_path / "project"
    init_project(project)
    fake = _fake_claude_subagent(tmp_path / "fake-claude")

    result = launch_execute_agent(
        project,
        task={"task_id": "task-c-1", "mode": "proof", "objective": "Prove it."},
        mode="proof",
        executor="claude",
        executable=str(fake),
    )

    assert result["status"] == "completed"
    assert result["executor"] == "claude"
    run_dir = project / result["agent_run_dir"]
    request = json.loads((run_dir / "request.json").read_text(encoding="utf-8"))
    assert request["executor"] == "claude"
    assert Path(request["codex_command"][0]).name == "fake-claude"
    # Events are captured into the (executor-neutral-in-practice) codex.events file,
    # rendered through the claude adapter, and the manifest is self-describing.
    assert (run_dir / "codex.events.jsonl").exists()
    log_manifest = json.loads((run_dir / "log_manifest.json").read_text(encoding="utf-8"))
    assert log_manifest["executor"] == "claude"
    assert log_manifest["log_adapter"] == "claude"
    assert log_manifest["session_id"] == "claude-sess-1"
    assert "claude agent completed" in (run_dir / "codex.log").read_text(encoding="utf-8")
    assert "fake claude stderr" in (run_dir / "codex.stderr.log").read_text(encoding="utf-8")


def test_execute_agent_claude_writes_exec_pgid(tmp_path):
    project = tmp_path / "project"
    init_project(project)
    fake = _fake_claude_subagent(tmp_path / "fake-claude")
    result = launch_execute_agent(
        project, task={"task_id": "t", "mode": "proof", "objective": "x"}, mode="proof",
        executor="claude", executable=str(fake),
    )
    status = json.loads((project / result["agent_run_dir"] / "status.json").read_text(encoding="utf-8"))
    # Canonical process field is now exec_pgid (codex_pgid kept only as a read fallback).
    assert "exec_pgid" in status


# ---------------------------------------------------------------------------
# Verification dispatch.
# ---------------------------------------------------------------------------

def _fake_claude_verifier(path: Path) -> Path:
    path.write_text(
        """#!/bin/sh
python - <<'PY'
import json, os
from pathlib import Path
root = Path(os.environ["ITERIS_PROJECT_ROOT"])
request_id = os.environ["ITERIS_VERIFICATION_REQUEST_ID"]
assert os.environ["ITERIS_EXECUTOR"] == "claude"
run_dir = root / "verification" / "agent_runs" / request_id
(run_dir / "verification.json").write_text(json.dumps({
    "verification_report": {"summary": "ok", "critical_errors": [], "gaps": []},
    "verdict": "correct", "repair_hints": "", "checked_artifacts": [], "checked_fact_ids": []
}), encoding="utf-8")
print(json.dumps({"type":"result","subtype":"success","session_id":"v1","result":"correct"}))
PY
""",
        encoding="utf-8",
    )
    path.chmod(0o755)
    return path


def test_verify_agent_claude_end_to_end(tmp_path):
    project = tmp_path / "project"
    init_project(project)
    fake = _fake_claude_verifier(tmp_path / "fake-claude")

    result = verify_agent(
        project, mode="fact", claim="A claim", artifacts=[], executor="claude", executable=str(fake)
    )
    assert result["passed"] is True
    assert result["executor"] == "claude"
    assert result["verification_scope"] == "claude_agent"
    assert result["verifier"] == "iteris.claude_verification_agent"


# ---------------------------------------------------------------------------
# Supervision judgment backend.
# ---------------------------------------------------------------------------

def test_verifier_processes_match_claude_via_env_tag():
    from iteris.commands.goal import _matching_verifier_processes

    project = "/proj"
    ps = "\n".join(
        [
            "201 1 node /usr/bin/claude -p --output-format stream-json --verbose --dangerously-skip-permissions",
            "202 1 node /usr/bin/claude -p --output-format stream-json --verbose",
            "203 1 node /bin/codex exec -C /proj -m gpt-5.5 --dangerously-bypass-approvals-and-sandbox -",
        ]
    )
    envs = {
        201: f"ITERIS_PROCESS_ROLE=verification_agent\nITERIS_PROJECT_ROOT={project}\n",  # claude verifier
        202: f"ITERIS_PROCESS_ROLE=subagent_explore\nITERIS_PROJECT_ROOT={project}\n",  # claude sub-agent, not a verifier
        203: "ITERIS_PROCESS_ROLE=verification_agent\n",  # codex verifier matched by argv -C
    }
    matches = _matching_verifier_processes(ps, project, env_by_pid=lambda pid: envs.get(pid, ""))
    assert sorted(m["pid"] for m in matches) == [201, 203]


def test_keystone_counts_credit_claude_agent():
    from iteris.commands.context import keystone_verification_counts

    results = [
        {"passed": True, "mode": "fact", "verification_scope": "claude_agent", "primary_fact_ids": ["fact:a"]},
        {"passed": True, "mode": "fact", "verification_scope": "codex_agent", "primary_fact_ids": ["fact:b"]},
        {"passed": True, "mode": "fact", "verification_scope": "agent_panel", "panel_runs": 2, "primary_fact_ids": ["fact:c"]},
        {"passed": True, "mode": "fact", "verification_scope": "structural_precheck", "primary_fact_ids": ["fact:d"]},
    ]
    counts = keystone_verification_counts(results)
    assert counts == {"fact:a": 1, "fact:b": 1, "fact:c": 2}


def test_engine_default_backend_is_executor_aware():
    # The default factory resolves the executor per call (inherits ITERIS_EXECUTOR).
    profile = Profile(name="p", sensors=[], triggers=[], contracts=[], actuators=[])
    assert profile.backend_factory is agent_backend


def test_generalize_analyze_print_uses_claude_command(tmp_path):
    from typer.testing import CliRunner
    from iteris.cli import app

    project = tmp_path / "proj"
    init_project(project)
    target = project / "results" / "proj" / "answer_verified.md"
    target.parent.mkdir(parents=True)
    target.write_text("# result\n", encoding="utf-8")
    (project / "STATUS.md").write_text("target_artifact: results/proj/answer_verified.md\n", encoding="utf-8")

    result = CliRunner().invoke(
        app, ["tool", "generalize", "analyze", str(project), "--print", "--executor", "claude", "--json"]
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["mode"] == "print"
    # The launched agent CLI is claude, not codex (the .iteris/codex_home/ path
    # is intentionally shared by both executors, so don't assert on the path).
    assert "claude --dangerously-skip-permissions" in payload["command"]
    assert "CLAUDE_CONFIG_DIR=" in payload["command"]
    assert " codex " not in payload["command"] and "codex exec" not in payload["command"]


def test_render_claude_events_round_trips(tmp_path):
    from iteris.log_adapters import render_claude_events

    events = tmp_path / "e.jsonl"
    events.write_text(
        "\n".join(
            json.dumps(e)
            for e in [
                {"type": "system", "subtype": "init", "session_id": "s1"},
                {"type": "assistant", "session_id": "s1", "message": {"content": [{"type": "text", "text": "hello"}, {"type": "tool_use", "id": "t1", "name": "Bash", "input": {"command": "ls"}}]}},
                {"type": "user", "session_id": "s1", "message": {"content": [{"type": "tool_result", "tool_use_id": "t1", "content": "out", "is_error": False}]}},
                {"type": "result", "subtype": "success", "session_id": "s1", "result": "done"},
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    out = render_claude_events(events, tmp_path / "rendered.log", header_lines=["h: 1"])
    assert out["event_count"] == 4
    assert out["malformed_line_count"] == 0
    assert out["session_id"] == "s1"
    text = (tmp_path / "rendered.log").read_text(encoding="utf-8")
    assert "hello" in text and "Bash" in text and "tool_result" in text


# ---------------------------------------------------------------------------
# Evolve launcher: the family must carry the executor through the
# detached tmux session, which otherwise inherits the tmux server's env and
# drops $ITERIS_EXECUTOR, defaulting master + judges + children to codex.
# ---------------------------------------------------------------------------

def test_evolve_run_detached_bakes_executor_into_tmux(tmp_path, monkeypatch):
    import iteris.commands.evolve as ev
    from iteris.evolve import init_state
    from typer.testing import CliRunner
    from iteris.cli import app

    root = tmp_path / "fam"
    init_project(root)
    init_state(root, goal="g", policy={"seed_veto_window_minutes": 0}, budget={"wall_hours": 10.0, "max_concurrent": 1})

    captured: dict[str, list[str]] = {}
    monkeypatch.setattr(ev, "_tmux_session_exists", lambda name: False)

    class _Result:
        returncode = 0

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        return _Result()

    monkeypatch.setattr(ev.subprocess, "run", fake_run)

    res = CliRunner().invoke(
        app, ["evolve", "run", str(root), "--executor", "claude", "--verification-executor", "codex"]
    )
    assert res.exit_code == 0, res.output
    inner = captured["cmd"][-1]  # the tmux new-session pane command
    assert "env ITERIS_EXECUTOR=claude" in inner
    assert "ITERIS_VERIFICATION_EXECUTOR=codex" in inner
    assert "--executor claude" in inner
    assert "--verification-executor codex" in inner


def test_evolve_run_foreground_pins_executor_in_environ(tmp_path, monkeypatch):
    import iteris.commands.evolve as ev
    import iteris.supervision.engine as engine
    from iteris.evolve import init_state
    from typer.testing import CliRunner
    from iteris.cli import app

    root = tmp_path / "fam"
    init_project(root)
    init_state(root, goal="g", policy={"seed_veto_window_minutes": 0}, budget={"wall_hours": 10.0, "max_concurrent": 1})

    # Baseline so monkeypatch restores env state on teardown (the CLI assigns
    # os.environ directly, which would otherwise leak into other tests).
    monkeypatch.setenv("ITERIS_EXECUTOR", "codex")
    seen: dict[str, str] = {}

    def fake_run_loop(root_arg, profile, **kwargs):
        seen["executor"] = os.environ.get("ITERIS_EXECUTOR", "")
        return 0

    monkeypatch.setattr(engine, "run_loop", fake_run_loop)

    res = CliRunner().invoke(app, ["evolve", "run", str(root), "--foreground", "--executor", "claude"])
    assert res.exit_code == 0, res.output
    assert seen["executor"] == "claude"


def test_evolve_executor_args_reads_env(monkeypatch):
    from iteris.supervision.profiles.evolve import _executor_args

    monkeypatch.setenv("ITERIS_EXECUTOR", "claude")
    assert _executor_args() == ["--executor", "claude"]
    monkeypatch.delenv("ITERIS_EXECUTOR", raising=False)
    assert _executor_args() == ["--executor", "codex"]


def test_last_agent_message_handles_codex_and_claude(tmp_path):
    # codex format A (thread/turn/item)
    codex_a = tmp_path / "a.jsonl"
    codex_a.write_text(json.dumps({"type": "item.completed", "item": {"type": "agent_message", "text": "codex reply"}}) + "\n", encoding="utf-8")
    assert _last_agent_message(codex_a) == "codex reply"

    # claude stream-json: result event carries the final text
    claude = tmp_path / "c.jsonl"
    claude.write_text(
        json.dumps({"type": "assistant", "message": {"content": [{"type": "text", "text": "intermediate"}]}}) + "\n"
        + json.dumps({"type": "result", "subtype": "success", "result": "final claude answer"}) + "\n",
        encoding="utf-8",
    )
    assert _last_agent_message(claude) == "final claude answer"

    # claude stream-json without a result event: fall back to last assistant text
    claude2 = tmp_path / "c2.jsonl"
    claude2.write_text(json.dumps({"type": "assistant", "message": {"content": [{"type": "text", "text": "only assistant"}]}}) + "\n", encoding="utf-8")
    assert _last_agent_message(claude2) == "only assistant"

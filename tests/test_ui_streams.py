from __future__ import annotations

import json

from typer.testing import CliRunner

from iteris.cli import app
from iteris.project import init_project, write_json

runner = CliRunner()


def _streams(project) -> list[dict]:
    result = runner.invoke(app, ["tool", "ui", "streams", str(project), "--json"])
    assert result.exit_code == 0, result.output
    return json.loads(result.output)


def test_streams_empty_project(tmp_path):
    project = tmp_path / "proj"
    project.mkdir()
    init_project(project)
    assert _streams(project) == []


def test_streams_surfaces_agent_run(tmp_path):
    project = tmp_path / "proj"
    project.mkdir()
    init_project(project)

    run_dir = project / "artifacts" / "agent_runs" / "execute-20260101T000000000000Z-task-demo"
    run_dir.mkdir(parents=True)
    write_json(run_dir / "request.json", {"role": "execute", "mode": "proof", "task_id": "task-demo"})
    write_json(run_dir / "status.json", {"status": "running", "pid": 999999})
    (run_dir / "codex.events.jsonl").write_text(
        json.dumps({"type": "item.completed", "item": {"type": "agent_message", "text": "hi"}}) + "\n",
        encoding="utf-8",
    )

    streams = _streams(project)
    agent = [s for s in streams if s["kind"] == "agent"]
    assert len(agent) == 1
    assert agent[0]["format"] == "structured"
    assert agent[0]["adapter"] == "codex"
    assert agent[0]["role"] == "execute"
    assert agent[0]["path"].endswith("codex.events.jsonl")
    # pid 999999 is almost certainly dead -> runtime auto-marks it not-live.
    assert agent[0]["live"] is False


def test_streams_surfaces_verifier(tmp_path):
    project = tmp_path / "proj"
    project.mkdir()
    init_project(project)

    vdir = project / "verification" / "agent_runs" / "verify-20260101T000000000000Z-fact-demo"
    vdir.mkdir(parents=True)
    (vdir / "codex.events.jsonl").write_text("{}\n", encoding="utf-8")

    streams = _streams(project)
    verify = [s for s in streams if s["kind"] == "verify"]
    assert len(verify) == 1
    assert verify[0]["live"] is True  # no verification.json yet -> in progress


def test_streams_surfaces_historical_rollouts(tmp_path):
    project = tmp_path / "proj"
    project.mkdir()
    init_project(project)

    for name in ("goal-s-20260101T000000000000Z", "goal-s-20260102T000000000000Z"):
        sessions = project / ".iteris" / "codex_home" / name / "sessions" / "2026" / "01"
        sessions.mkdir(parents=True)
        (sessions / "rollout-demo.jsonl").write_text("{}\n", encoding="utf-8")

    streams = _streams(project)
    rollouts = [s for s in streams if s["id"].startswith("rollout:")]
    assert len(rollouts) == 2
    assert all(s["format"] == "structured" for s in rollouts)
    assert all(s["adapter"] == "codex" for s in rollouts)
    # No tmux session/meta in a fresh project -> nothing is live.
    assert all(s["live"] is False for s in rollouts)


def test_streams_surfaces_claude_transcripts(tmp_path):
    project = tmp_path / "proj"
    project.mkdir()
    init_project(project)

    run_name = "goal-s-20260103T000000000000Z"
    transcript = project / ".iteris" / "codex_home" / run_name / "projects" / "-tmp-proj" / "session.jsonl"
    transcript.parent.mkdir(parents=True)
    transcript.write_text(
        json.dumps(
            {
                "type": "assistant",
                "sessionId": "s1",
                "timestamp": "2026-01-03T00:00:00Z",
                "message": {"role": "assistant", "content": [{"type": "text", "text": "hello"}]},
            }
        )
        + "\n",
        encoding="utf-8",
    )
    logs = project / ".iteris" / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    write_json(logs / f"{run_name}.meta.json", {"session_name": "s", "executor": "claude"})

    streams = _streams(project)
    rollouts = [s for s in streams if s["id"].startswith("rollout:claude:")]
    assert len(rollouts) == 1
    assert rollouts[0]["format"] == "structured"
    assert rollouts[0]["adapter"] == "claude"
    assert rollouts[0]["path"].endswith("projects/-tmp-proj/session.jsonl")


def test_snapshot_infers_claude_adapter_from_project_transcript_path(tmp_path):
    project = tmp_path / "proj"
    project.mkdir()
    init_project(project)

    transcript = (
        project
        / ".iteris"
        / "codex_home"
        / "goal-s-20260103T000000000000Z"
        / "projects"
        / "-tmp-proj"
        / "session.jsonl"
    )
    transcript.parent.mkdir(parents=True)
    transcript.write_text(
        json.dumps(
            {
                "type": "assistant",
                "sessionId": "s1",
                "timestamp": "2026-01-03T00:00:00Z",
                "message": {"role": "assistant", "content": [{"type": "text", "text": "hello claude"}]},
            }
        )
        + "\n",
        encoding="utf-8",
    )

    result = runner.invoke(app, ["tool", "ui", "snapshot", str(transcript.relative_to(project)), str(project), "--json"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["format"] == "structured"
    assert payload["adapter"] == "claude"
    assert [e["content"] for e in payload["entries"]] == ["hello claude"]


def test_streams_use_pane_meta_without_exposing_raw_pane_logs(tmp_path):
    project = tmp_path / "proj"
    project.mkdir()
    init_project(project)

    logs = project / ".iteris" / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    for run_name, session in (
        ("goal-iteris-proj-20260101T000000000000Z", "iteris-proj"),
        ("goal-iteris-analyze-proj-20260102T000000000000Z", "iteris-analyze-proj"),
    ):
        (logs / f"{run_name}.pane.log").write_text("x\n", encoding="utf-8")
        write_json(logs / f"{run_name}.meta.json", {"session_name": session})
        sessions = project / ".iteris" / "codex_home" / run_name / "sessions" / "2026" / "01"
        sessions.mkdir(parents=True)
        (sessions / "rollout-demo.jsonl").write_text("{}\n", encoding="utf-8")

    streams = _streams(project)
    assert all(not s["id"].startswith("pane:") for s in streams)
    assert all(s["format"] == "structured" for s in streams)
    titles = {s["title"] for s in streams if s["id"].startswith("rollout:codex:")}
    assert "iteris-analyze-proj (Codex)" in titles


def test_snapshot_tail_entries_and_max_bytes(tmp_path):
    project = tmp_path / "proj"
    project.mkdir()
    init_project(project)

    events = project / "codex.events.jsonl"
    lines = [
        json.dumps({"type": "item.completed", "item": {"type": "agent_message", "text": f"m{i}"}})
        for i in range(4)
    ]
    events.write_text("\n".join(lines) + "\n", encoding="utf-8")

    result = runner.invoke(
        app, ["tool", "ui", "snapshot", "codex.events.jsonl", str(project), "--json", "--tail-entries", "2"]
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["format"] == "structured"
    assert payload["adapter"] == "codex"
    assert payload["truncated"] is True
    assert [e["content"] for e in payload["entries"]] == ["m2", "m3"]

    first_two = len(("\n".join(lines[:2]) + "\n").encode())
    result = runner.invoke(
        app, ["tool", "ui", "snapshot", "codex.events.jsonl", str(project), "--json", "--max-bytes", str(first_two)]
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert [e["content"] for e in payload["entries"]] == ["m0", "m1"]


def test_snapshot_rejects_symlink_escape(tmp_path):
    project = tmp_path / "proj"
    project.mkdir()
    init_project(project)

    secret = tmp_path / "outside-secret.json"
    secret.write_text("{}", encoding="utf-8")
    link = project / "leak.jsonl"
    link.symlink_to(secret)

    result = runner.invoke(app, ["tool", "ui", "snapshot", "leak.jsonl", str(project), "--json"])
    assert result.exit_code != 0

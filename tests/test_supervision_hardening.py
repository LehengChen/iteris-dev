"""Tests for the supervision-hardening batch: stale-verification detection,
worker CLI PATH pinning, high-priority message pane delivery, and the goal
contract clauses backing them."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest
from typer.testing import CliRunner

from iteris.cli import app
from iteris.codex_logs import build_child_env
from iteris.commands.context import build_context
from iteris.commands.goal import build_goal_prompt
from iteris.commands.message import notify_run_session
from iteris.commands.workflow import _attention_summary
from iteris.project import init_project, write_json
from iteris.verification.local import stale_verification_requests

runner = CliRunner()


@pytest.fixture()
def project(tmp_path: Path) -> Path:
    root = tmp_path / "proj"
    init_project(root)
    return root


def _write_request(project: Path, request_id: str, *, age_minutes: float) -> Path:
    path = project / "verification" / "requests" / f"{request_id}.json"
    write_json(path, {"request_id": request_id, "mode": "fact", "claim": "test claim"})
    stamp = time.time() - age_minutes * 60.0
    os.utime(path, (stamp, stamp))
    return path


# ---------------------------------------------------------------------------
# Stale verification requests


def test_stale_verification_request_without_result_is_flagged(project: Path) -> None:
    _write_request(project, "verify-dead", age_minutes=45.0)
    stale = stale_verification_requests(project)
    assert [item["request_id"] for item in stale] == ["verify-dead"]
    entry = stale[0]
    assert entry["age_minutes"] >= 44.0
    # No process carries this request id; on /proc platforms that is a
    # definitive "dead", elsewhere unknown.
    assert entry["verifier_process_alive"] in (False, None)


def test_completed_and_young_requests_are_not_flagged(project: Path) -> None:
    _write_request(project, "verify-done", age_minutes=120.0)
    write_json(project / "verification" / "results" / "verify-done.json", {"request_id": "verify-done", "passed": True})
    _write_request(project, "verify-fresh", age_minutes=1.0)
    assert stale_verification_requests(project) == []


def test_crashed_panel_seat_of_completed_panel_is_not_flagged(project: Path) -> None:
    # A seat that crashes never writes its own result, but the panel aggregate
    # already counts the crash as a rejection; once the aggregate result
    # exists, the seat's leftover request must not nag attention forever.
    _write_request(project, "verify-seat-crashed", age_minutes=45.0)
    panel_path = project / "verification" / "requests" / "verify-panel-x.json"
    write_json(
        panel_path,
        {"request_id": "verify-panel-x", "backend": "panel", "seat_request_ids": ["verify-seat-crashed", "verify-seat-ok"]},
    )
    write_json(project / "verification" / "results" / "verify-panel-x.json", {"request_id": "verify-panel-x", "passed": False})
    assert stale_verification_requests(project) == []

    # While the panel has no aggregate result yet, a dead seat is still live news.
    (project / "verification" / "results" / "verify-panel-x.json").unlink()
    assert [item["request_id"] for item in stale_verification_requests(project)] == ["verify-seat-crashed"]


def test_stale_verifications_surface_in_context_attention(project: Path) -> None:
    _write_request(project, "verify-dead", age_minutes=45.0)
    attention = build_context(project)["attention"]
    assert [item["request_id"] for item in attention["stale_verifications"]] == ["verify-dead"]
    assert "verify finalize" in attention["guidance"]
    summary = _attention_summary(attention)
    assert "verification request(s) without result" in summary


# ---------------------------------------------------------------------------
# Worker CLI PATH pinning


def test_build_child_env_pins_interpreter_scripts_dir(monkeypatch: pytest.MonkeyPatch) -> None:
    # Start from a PATH that lacks the scripts dir: the pin must add it, not
    # merely inherit it from a developer venv where it is already present.
    monkeypatch.setenv("PATH", "/usr/bin" + os.pathsep + "/bin")
    env = build_child_env({"ITERIS_PROCESS_ROLE": "verification_agent"})
    scripts_dir = str(Path(sys.executable).parent)
    assert env["PATH"].split(os.pathsep)[0] == scripts_dir
    assert env["ITERIS_PROCESS_ROLE"] == "verification_agent"


def test_build_child_env_does_not_duplicate_scripts_dir(monkeypatch: pytest.MonkeyPatch) -> None:
    scripts_dir = str(Path(sys.executable).parent)
    monkeypatch.setenv("PATH", scripts_dir + os.pathsep + "/opt/elsewhere/bin")
    env = build_child_env({})
    assert env["PATH"].split(os.pathsep).count(scripts_dir) == 1


# ---------------------------------------------------------------------------
# High-priority message pane delivery


def test_high_priority_send_reports_delivery_attempt(project: Path, tmp_path: Path) -> None:
    sender = tmp_path / "sender"
    init_project(sender)
    result = runner.invoke(
        app,
        [
            "tool", "message", "send", str(sender),
            "--to", str(project),
            "--body", "drain the review queue",
            "--type", "hint",
            "--priority", "high",
            "--sender", "human",
            "--json",
        ],
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    delivery = payload["delivery"]
    # No live run session exists for the temp project: the send must still
    # succeed, with the pane notice reported undelivered rather than raising.
    assert delivery["delivered"] is False
    assert "no live run session" in delivery["reason"]


def test_normal_priority_send_skips_pane_delivery(project: Path, tmp_path: Path) -> None:
    sender = tmp_path / "sender"
    init_project(sender)
    result = runner.invoke(
        app,
        ["tool", "message", "send", str(sender), "--to", str(project), "--body", "fyi", "--json"],
    )
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["delivery"] is None


def test_notify_run_session_without_live_session(project: Path) -> None:
    message = {"msg_id": "msg-x", "priority": "high", "type": "hint"}
    delivery = notify_run_session(project, message)
    assert delivery["delivered"] is False
    assert delivery["session_name"].startswith("iteris-")


def test_notify_run_session_sends_text_then_separate_enter(project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import iteris.commands.message as message_mod

    calls: list[list[str]] = []

    def fake_run(cmd: list[str], **kwargs: object) -> object:
        calls.append(list(cmd))
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(message_mod, "tmux_session_alive", lambda name: True)
    monkeypatch.setattr(message_mod.subprocess, "run", fake_run)
    # The verify step captures the pane; report it empty so the notice is
    # treated as submitted and no Enter retry is needed (delivered is True).
    monkeypatch.setattr(message_mod, "capture_pane", lambda name, **kwargs: "")
    # Don't actually sleep through the inter-keystroke delay in tests.
    monkeypatch.setattr(message_mod.time, "sleep", lambda _seconds: None)
    delivery = notify_run_session(project, {"msg_id": "msg-x", "priority": "high", "type": "hint"})
    assert delivery["delivered"] is True
    # Text and Enter are TWO separate send-keys calls: the Enter is a distinct
    # keystroke after a delay, so the worker CLI's paste-handling can't swallow
    # it (as it does when text+Enter ride in one call).
    assert len(calls) == 2
    text_cmd, enter_cmd = calls
    # First call: literal (-l) notice line carrying the msg_id and ack hint.
    assert text_cmd[:4] == ["tmux", "send-keys", "-t", delivery["session_name"]]
    assert text_cmd[4] == "-l"
    assert "msg-x" in text_cmd[-1] and "message ack" in text_cmd[-1]
    # Second call: a bare Enter keystroke, submitted on its own.
    assert enter_cmd == ["tmux", "send-keys", "-t", delivery["session_name"], "Enter"]


def test_notify_run_session_swallows_unexpected_errors(project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import iteris.commands.message as message_mod

    def boom(name: str) -> bool:
        raise RuntimeError("tmux exploded unexpectedly")

    monkeypatch.setattr(message_mod, "tmux_session_alive", boom)
    delivery = notify_run_session(project, {"msg_id": "msg-x", "priority": "high", "type": "hint"})
    assert delivery["delivered"] is False
    assert "tmux exploded unexpectedly" in delivery["reason"]


# ---------------------------------------------------------------------------
# Goal contract clauses


def test_goal_contract_carries_new_supervision_clauses() -> None:
    prompt = build_goal_prompt("Solve it.", target_artifact="results/p/answer_verified.md", problem_id="p")
    assert "attention.stale_verifications" in prompt
    assert "iteris tool verify finalize" in prompt
    assert "iteris tool message ack" in prompt
    assert "unread_messages" in prompt
    assert "more than 5 tasks sit in `review`" in prompt
    assert "Certify before building on sampling evidence" in prompt

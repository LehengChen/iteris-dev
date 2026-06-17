"""Messages module: lifecycle, queries, CLI, and piggyback surfaces."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest
from typer.testing import CliRunner

from iteris.cli import app
from iteris.commands.context import build_context
from iteris.messages import (
    MessageError,
    ack,
    list_messages,
    send,
    unacked,
    unread_summary,
)
from iteris.project import init_project

runner = CliRunner()


@pytest.fixture()
def project(tmp_path):
    root = tmp_path / "proj"
    init_project(root)
    return root


def test_send_list_ack_lifecycle(project):
    sent = send(project, body="Check the slab estimate against fact:x:y.", refs=["fact:x:y"])
    assert sent["msg_id"].startswith("msg-")
    assert sent["from"] == "supervisor"

    rows = list_messages(project, unread_only=True)
    assert len(rows) == 1 and not rows[0]["acked"]

    entry = ack(project, msg_id=sent["msg_id"], disposition="applied", note="re-checked")
    assert entry["disposition"] == "applied"

    assert list_messages(project, unread_only=True) == []
    merged = list_messages(project)
    assert merged[0]["acked"] and merged[0]["ack"]["disposition"] == "applied"

    # Two files, single writer each.
    assert (project / "messages" / "inbox.jsonl").exists()
    assert (project / "messages" / "ack.jsonl").exists()


def test_validation_errors(project):
    with pytest.raises(MessageError):
        send(project, body="   ")
    with pytest.raises(MessageError):
        send(project, body="x", type="command")
    with pytest.raises(MessageError):
        send(project, body="x", priority="urgent")
    with pytest.raises(MessageError):
        send(project, body="x", sender="root")
    with pytest.raises(MessageError):
        ack(project, msg_id="msg-nonexistent", disposition="applied")
    sent = send(project, body="real message")
    with pytest.raises(MessageError):
        ack(project, msg_id=sent["msg_id"], disposition="done")
    ack(project, msg_id=sent["msg_id"], disposition="noted")
    with pytest.raises(MessageError):  # double ack
        ack(project, msg_id=sent["msg_id"], disposition="applied")


def test_unacked_escalation_query(project):
    low = send(project, body="low priority lead")
    high = send(project, body="high priority steer", priority="high")

    assert {row["msg_id"] for row in unacked(project)} == {low["msg_id"], high["msg_id"]}
    assert [row["msg_id"] for row in unacked(project, min_priority="high")] == [high["msg_id"]]

    # Nothing is older than 6h yet; with a future reference clock everything is.
    future = datetime.now(timezone.utc) + timedelta(hours=7)
    assert unacked(project, min_priority="high", older_than_hours=6.0) == []
    overdue = unacked(project, min_priority="high", older_than_hours=6.0, now=future)
    assert [row["msg_id"] for row in overdue] == [high["msg_id"]]

    ack(project, msg_id=high["msg_id"], disposition="applied")
    assert unacked(project, min_priority="high", older_than_hours=6.0, now=future) == []


def test_unread_summary_and_context_piggyback(project):
    assert unread_summary(project) is None
    context = build_context(project)
    assert context["unread_messages"] is None

    high = send(project, body="one", priority="high")
    send(project, body="two")
    summary = unread_summary(project)
    assert summary["count"] == 2
    assert summary["high"] == 1
    assert summary["next"] == "iteris tool message list . --unread --json"
    # high-priority bodies are inlined so workers see directives without polling
    assert [item["msg_id"] for item in summary["high_messages"]] == [high["msg_id"]]
    assert summary["high_messages"][0]["body"] == "one"
    assert "ack" in summary["ack_command"]
    assert build_context(project)["unread_messages"] == summary


def test_cli_round_trip(project, tmp_path):
    sender = tmp_path / "rootproj"
    init_project(sender)

    result = runner.invoke(
        app,
        [
            "tool", "message", "send", str(sender),
            "--to", str(project), "--body", "steer toward the pole-removed kernel",
            "--priority", "high", "--ref", "dir-01", "--json",
        ],
    )
    assert result.exit_code == 0, result.output
    sent = json.loads(result.output)
    assert sent["priority"] == "high" and sent["refs"] == ["dir-01"]

    result = runner.invoke(app, ["tool", "message", "list", str(project), "--unread", "--json"])
    assert result.exit_code == 0, result.output
    listed = json.loads(result.output)
    assert listed["count"] == 1

    result = runner.invoke(
        app,
        [
            "tool", "message", "ack", str(project),
            "--msg-id", sent["msg_id"], "--disposition", "applied", "--json",
        ],
    )
    assert result.exit_code == 0, result.output

    result = runner.invoke(app, ["tool", "message", "list", str(project), "--unread", "--json"])
    assert json.loads(result.output)["count"] == 0

    # Invalid disposition surfaces as a CLI parameter error, not a traceback.
    result = runner.invoke(
        app,
        [
            "tool", "message", "ack", str(project),
            "--msg-id", sent["msg_id"], "--disposition", "bogus",
        ],
    )
    assert result.exit_code != 0

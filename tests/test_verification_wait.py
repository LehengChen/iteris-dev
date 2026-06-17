"""Tests for `iteris tool verify wait` — the always-returns verdict primitive.

It replaces hand-written `until [ -f results/<id>.json ]; do sleep ...; done`
waiter shells that deadlocked the /goal loop when the result landed unseen or
the verifier died.
"""

from __future__ import annotations

import json

from typer.testing import CliRunner

import iteris.commands.verification as verification_command
from iteris.commands.verification import app


def _init_verification_project(root):
    (root / ".iteris").mkdir(parents=True)
    (root / "iteris.toml").write_text("[project]\nid = \"p\"\n", encoding="utf-8")
    (root / "verification" / "requests").mkdir(parents=True)
    (root / "verification" / "results").mkdir(parents=True)


def test_verify_wait_returns_landed_verdict(tmp_path):
    project = tmp_path / "p"
    _init_verification_project(project)
    rid = "verify-20260101T000000Z-fact-x"
    (project / "verification" / "results" / f"{rid}.json").write_text(
        json.dumps({"request_id": rid, "passed": True, "verdict": "accepted", "summary": "ok"}),
        encoding="utf-8",
    )

    result = CliRunner().invoke(app, ["wait", str(project), "--request-id", rid, "--json"])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["status"] == "done"
    assert payload["passed"] is True
    assert payload["verdict"] == "accepted"


def test_verify_wait_reports_dead_verifier(tmp_path, monkeypatch):
    project = tmp_path / "p"
    _init_verification_project(project)
    rid = "verify-20260101T000001Z-fact-y"
    # Request exists, no result, and the verifier process is gone.
    (project / "verification" / "requests" / f"{rid}.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(verification_command, "_verifier_process_alive", lambda _id: False)

    result = CliRunner().invoke(app, ["wait", str(project), "--request-id", rid, "--json"])

    assert result.exit_code == 1, result.output
    payload = json.loads(result.output)
    assert payload["status"] == "dead"
    assert "verify finalize" in payload["salvage"]


def test_verify_wait_times_out_without_hanging(tmp_path, monkeypatch):
    project = tmp_path / "p"
    _init_verification_project(project)
    rid = "verify-20260101T000002Z-fact-z"
    (project / "verification" / "requests" / f"{rid}.json").write_text("{}", encoding="utf-8")
    # Verifier still alive (process check True) but no result before the deadline.
    monkeypatch.setattr(verification_command, "_verifier_process_alive", lambda _id: True)

    result = CliRunner().invoke(
        app, ["wait", str(project), "--request-id", rid, "--timeout", "0", "--json"]
    )

    assert result.exit_code == 124, result.output
    payload = json.loads(result.output)
    assert payload["status"] == "timeout"
    assert payload["timed_out"] is True


def test_verify_wait_rejects_path_traversal(tmp_path):
    project = tmp_path / "p"
    _init_verification_project(project)
    result = CliRunner().invoke(app, ["wait", str(project), "--request-id", "../escape", "--json"])
    assert result.exit_code != 0

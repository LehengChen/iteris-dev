"""Tests for liveness scanning, crash recovery, agent wait, and attention signals."""

from __future__ import annotations

import json
import os
from pathlib import Path

from typer.testing import CliRunner

import iteris.commands.workflow as workflow_command
from iteris.cli import app
from iteris.liveness import scan_agent_runs, scan_project_liveness
from iteris.memory.facts import validate_project_facts, write_fact
from iteris.project import init_project, now_iso, write_json
from iteris.tasks import load_task_pool, stale_status_tasks, upsert_pool_task
from iteris.verification.local import claim_streak_key, rejection_streaks

runner = CliRunner()

DEAD_PID = 2**22 + 12345  # beyond default pid_max, never a live process


def _make_project(tmp_path: Path) -> Path:
    source = tmp_path / "problem.tex"
    source.write_text("\\begin{problem}Test.\\end{problem}", encoding="utf-8")
    project = tmp_path / "project"
    project.mkdir()
    init_project(project, source=source)
    return project


OLD_TIMESTAMP = "2026-01-01T00:00:00Z"  # far older than the launch grace window


def _make_agent_run(
    project: Path,
    run_id: str,
    *,
    status: str,
    pid: int | None,
    task_id: str | None = None,
    updated_at: str = OLD_TIMESTAMP,
) -> Path:
    run_dir = project / "artifacts" / "agent_runs" / run_id
    run_dir.mkdir(parents=True)
    write_json(run_dir / "request.json", {"run_id": run_id, "task_id": task_id, "role": "execute", "project_path": str(project)})
    payload = {"schema_version": "iteris.agent_run_status.v0", "status": status, "updated_at": updated_at}
    if pid is not None:
        payload["pid"] = pid
    write_json(run_dir / "status.json", payload)
    return run_dir


def test_scan_agent_runs_classifies_live_and_orphaned(tmp_path):
    project = _make_project(tmp_path)
    _make_agent_run(project, "execute-1-live", status="running", pid=os.getpid(), task_id="task-live")
    _make_agent_run(project, "execute-2-dead", status="running", pid=DEAD_PID, task_id="task-dead")
    _make_agent_run(project, "execute-3-no-pid", status="running", pid=None, task_id="task-no-pid")
    _make_agent_run(project, "execute-4-done", status="completed", pid=DEAD_PID, task_id="task-done")

    result = scan_agent_runs(project)
    assert [entry["run_id"] for entry in result["live"]] == ["execute-1-live"]
    orphaned_ids = {entry["run_id"] for entry in result["orphaned"]}
    assert orphaned_ids == {"execute-2-dead", "execute-3-no-pid"}


def test_scan_agent_runs_grace_window_protects_fresh_launches(tmp_path):
    project = _make_project(tmp_path)
    _make_agent_run(project, "execute-launching", status="pending", pid=None, task_id="task-x", updated_at=now_iso())
    result = scan_agent_runs(project)
    assert [entry["run_id"] for entry in result["live"]] == ["execute-launching"]
    assert result["orphaned"] == []


def test_unassigned_running_task_is_healthy_while_session_lives(tmp_path, monkeypatch):
    import iteris.liveness as liveness_module

    project = _make_project(tmp_path)
    upsert_pool_task(project, task_id="task-inline", mode="proof", objective="In-session work.", status="running")
    monkeypatch.setattr(liveness_module, "tmux_session_alive", lambda name: True)
    liveness = scan_project_liveness(project, session_name="iteris-live")
    assert liveness["orphaned_pool_tasks"] == []
    assert liveness["needs_recovery"] is False

    monkeypatch.setattr(liveness_module, "tmux_session_alive", lambda name: False)
    liveness = scan_project_liveness(project, session_name="iteris-dead")
    assert {entry["task_id"] for entry in liveness["orphaned_pool_tasks"]} == {"task-inline"}


def test_recover_moves_completed_unharvested_task_to_review(tmp_path):
    project = _make_project(tmp_path)
    run_dir = _make_agent_run(project, "execute-finished", status="completed", pid=DEAD_PID, task_id="task-finished")
    (run_dir / "output.md").write_text("results", encoding="utf-8")
    write_json(run_dir / "output.json", {"status": "completed"})
    upsert_pool_task(project, task_id="task-finished", mode="proof", objective="Finished.", status="running", assigned_agent_run="execute-finished")

    result = runner.invoke(app, ["recover", str(project), "--json"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    actions = {(item["action"], item["task_id"]) for item in payload["actions"]}
    assert ("move_task_to_review", "task-finished") in actions

    pool = load_task_pool(project)
    task = next(item for item in pool["tasks"] if item["task_id"] == "task-finished")
    assert task["status"] == "review"
    assert task["assigned_agent_run"] == "execute-finished"


def test_repair_orphaned_task_skips_when_task_changed_since_scan(tmp_path):
    from iteris.tasks import repair_orphaned_task

    project = _make_project(tmp_path)
    upsert_pool_task(project, task_id="task-x", mode="proof", objective="X.", status="review", assigned_agent_run="execute-new")
    # Scan believed the task was running and assigned to execute-old.
    result = repair_orphaned_task(
        project,
        "task-x",
        to_status="ready",
        note="should not apply",
        expected_assigned_run="execute-old",
        clear_assigned_run=True,
    )
    assert result is None
    pool = load_task_pool(project)
    task = next(item for item in pool["tasks"] if item["task_id"] == "task-x")
    assert task["status"] == "review"
    assert task["assigned_agent_run"] == "execute-new"


def test_scan_project_liveness_flags_orphaned_running_tasks(tmp_path):
    project = _make_project(tmp_path)
    _make_agent_run(project, "execute-live", status="running", pid=os.getpid(), task_id="task-alive")
    _make_agent_run(project, "execute-dead", status="running", pid=DEAD_PID, task_id="task-orphan")
    upsert_pool_task(project, task_id="task-alive", mode="proof", objective="Alive.", status="running", assigned_agent_run="execute-live")
    upsert_pool_task(project, task_id="task-orphan", mode="proof", objective="Orphan.", status="running", assigned_agent_run="execute-dead")
    upsert_pool_task(project, task_id="task-idle", mode="proof", objective="Idle.", status="ready")

    liveness = scan_project_liveness(project, session_name="iteris-nonexistent-session-xyz")
    assert liveness["session_live"] is False
    # With the owning loop gone, a worker still alive is itself an orphan (no
    # harvester) and so is its task — both must be flagged for recovery.
    orphaned_run_ids = {entry["run_id"] for entry in liveness["orphaned_agent_runs"]}
    assert orphaned_run_ids == {"execute-dead", "execute-live"}
    live_orphan = next(e for e in liveness["orphaned_agent_runs"] if e["run_id"] == "execute-live")
    assert live_orphan["pid_alive"] is True
    assert {entry["task_id"] for entry in liveness["orphaned_pool_tasks"]} == {"task-orphan", "task-alive"}
    assert liveness["needs_recovery"] is True


def test_recover_marks_dead_runs_failed_and_resets_tasks(tmp_path):
    project = _make_project(tmp_path)
    _make_agent_run(project, "execute-dead", status="running", pid=DEAD_PID, task_id="task-orphan")
    upsert_pool_task(project, task_id="task-orphan", mode="proof", objective="Orphan.", status="running", assigned_agent_run="execute-dead")

    dry = runner.invoke(app, ["recover", str(project), "--dry-run", "--json"])
    assert dry.exit_code == 0, dry.output
    status_after_dry = json.loads((project / "artifacts" / "agent_runs" / "execute-dead" / "status.json").read_text(encoding="utf-8"))
    assert status_after_dry["status"] == "running"

    result = runner.invoke(app, ["recover", str(project), "--json"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    actions = {(item["action"], item.get("run_id") or item.get("task_id")) for item in payload["actions"]}
    assert ("mark_agent_run_failed", "execute-dead") in actions
    assert ("reset_task_to_ready", "task-orphan") in actions

    status_after = json.loads((project / "artifacts" / "agent_runs" / "execute-dead" / "status.json").read_text(encoding="utf-8"))
    assert status_after["status"] == "failed"
    pool = load_task_pool(project)
    task = next(item for item in pool["tasks"] if item["task_id"] == "task-orphan")
    assert task["status"] == "ready"
    assert task["assigned_agent_run"] is None
    assert any("recover" in note for note in task["notes"])


def test_recover_leaves_live_runs_untouched(tmp_path, monkeypatch):
    import iteris.liveness as liveness_module

    project = _make_project(tmp_path)
    _make_agent_run(project, "execute-live", status="running", pid=os.getpid(), task_id="task-alive")
    upsert_pool_task(project, task_id="task-alive", mode="proof", objective="Alive.", status="running", assigned_agent_run="execute-live")
    # While the owning /goal loop is alive, a live worker is healthy and must be
    # left running and never signaled (only a dead-loop orphan gets reaped).
    monkeypatch.setattr(liveness_module, "tmux_session_alive", lambda name: True)

    result = runner.invoke(app, ["recover", str(project), "--json"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["actions"] == []
    status = json.loads((project / "artifacts" / "agent_runs" / "execute-live" / "status.json").read_text(encoding="utf-8"))
    assert status["status"] == "running"


def test_agent_wait_returns_terminal_status_immediately(tmp_path):
    project = _make_project(tmp_path)
    run_dir = _make_agent_run(project, "execute-done", status="completed", pid=None, task_id="task-x")
    (run_dir / "output.md").write_text("done", encoding="utf-8")
    write_json(run_dir / "output.json", {"status": "completed"})
    result = runner.invoke(app, ["tool", "agent", "wait", str(project), "--run-id", "execute-done", "--timeout", "5", "--json"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["status"] == "completed"
    assert payload["timed_out"] is False


def test_agent_wait_resolves_dead_worker_instead_of_hanging(tmp_path):
    project = _make_project(tmp_path)
    _make_agent_run(project, "execute-dead", status="running", pid=DEAD_PID, task_id="task-x")
    result = runner.invoke(app, ["tool", "agent", "wait", str(project), "--run-id", "execute-dead", "--timeout", "30", "--json"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["status"] == "exited_unknown"
    assert payload["timed_out"] is False


def test_agent_wait_times_out_on_live_worker(tmp_path):
    project = _make_project(tmp_path)
    _make_agent_run(project, "execute-live", status="running", pid=os.getpid(), task_id="task-x")
    result = runner.invoke(
        app,
        ["tool", "agent", "wait", str(project), "--run-id", "execute-live", "--timeout", "1", "--poll-interval", "0.5", "--json"],
    )
    assert result.exit_code == 124
    payload = json.loads(result.output)
    assert payload["timed_out"] is True
    assert payload["status"] == "running"


def test_stale_status_tasks_flags_old_running_and_review(tmp_path):
    pool = {
        "tasks": [
            {"task_id": "task-old-review", "status": "review", "updated_at": "2026-06-12T00:00:00Z"},
            {"task_id": "task-old-running", "status": "running", "updated_at": "2026-06-12T01:00:00Z"},
            {"task_id": "task-fresh", "status": "review", "updated_at": "2026-06-12T09:30:00Z"},
            {"task_id": "task-done", "status": "done", "updated_at": "2026-06-11T00:00:00Z"},
            {"task_id": "task-no-ts", "status": "review"},
        ]
    }
    stale = stale_status_tasks(pool, now="2026-06-12T10:00:00Z")
    ids = [item["task_id"] for item in stale]
    assert ids == ["task-old-review", "task-old-running"]
    assert stale[0]["hours_in_status"] == 10.0


def test_rejection_streaks_groups_claim_revisions(tmp_path):
    def result(claim: str, verdict: str, stamp: str) -> dict:
        return {"mode": "fact", "claim": claim, "verdict": verdict, "created_at": stamp, "request_id": f"verify-{stamp}"}

    results = [
        result("Verify Lemma E revision 1 in artifacts/proofs", "rejected", "2026-06-12T01:00:00Z"),
        result("Verify Lemma E revision 2 in artifacts/proofs", "rejected", "2026-06-12T02:00:00Z"),
        result("Verify Lemma E revision 3 in artifacts/proofs", "rejected", "2026-06-12T03:00:00Z"),
        result("Verify the load budget bound", "accepted", "2026-06-12T04:00:00Z"),
    ]
    streaks = rejection_streaks(results, min_streak=3)
    assert len(streaks) == 1
    assert streaks[0]["consecutive_rejections"] == 3
    assert streaks[0]["attempts"] == 3

    # An acceptance terminates the streak.
    results.append(result("Verify Lemma E revision 4 in artifacts/proofs", "accepted", "2026-06-12T05:00:00Z"))
    assert rejection_streaks(results, min_streak=3) == []


def test_rejection_streaks_group_by_shared_fact_id_under_paraphrase(tmp_path):
    # The agent rewords the claim every round, so the normalized claim
    # key splits — but the checked fact id is stable, so a union by fact id keeps
    # the streak whole instead of reporting it as separate sub-3 groups.
    def result(claim: str, stamp: str, errors: int) -> dict:
        return {
            "mode": "fact",
            "claim": claim,
            "verdict": "rejected",
            "created_at": stamp,
            "request_id": f"verify-{stamp}",
            "checked_fact_ids": ["fact:proj:keystone"],
            "critical_errors": [{"location": "x", "issue": "e"}] * errors,
            "gaps": [],
        }

    results = [
        result("The boundary escape coefficient is negative everywhere", "2026-06-12T01:00:00Z", 3),
        result("Every terminal stall admits a polar-transpose escape", "2026-06-12T02:00:00Z", 2),
        result("Show a2 < 0 on the surviving chart family", "2026-06-12T03:00:00Z", 1),
    ]
    streaks = rejection_streaks(results, min_streak=3)
    assert len(streaks) == 1
    s = streaks[0]
    assert s["consecutive_rejections"] == 3
    assert s["grouped_by"] == "checked_fact_ids"
    assert s["checked_fact_ids"] == ["fact:proj:keystone"]
    # Converging repair: error count shrank 3 -> 2 -> 1.
    assert s["error_gap_counts"] == [3, 2, 1]
    assert s["error_gap_trend"] == "converging"


def test_rejection_streak_trend_distinguishes_stuck_from_converging():
    def result(stamp: str, errors: int) -> dict:
        return {
            "mode": "fact",
            "claim": "same claim text every round",
            "verdict": "rejected",
            "created_at": stamp,
            "request_id": f"verify-{stamp}",
            "checked_fact_ids": ["fact:proj:stuck"],
            "critical_errors": [{"location": "x", "issue": "e"}] * errors,
        }

    worsening = rejection_streaks(
        [result("2026-06-12T01:00:00Z", 1), result("2026-06-12T02:00:00Z", 2), result("2026-06-12T03:00:00Z", 3)],
        min_streak=3,
    )
    assert worsening[0]["error_gap_trend"] == "worsening"
    flat = rejection_streaks(
        [result("2026-06-12T01:00:00Z", 2), result("2026-06-12T02:00:00Z", 2), result("2026-06-12T03:00:00Z", 2)],
        min_streak=3,
    )
    assert flat[0]["error_gap_trend"] == "flat"


def test_claim_streak_key_strips_revision_markers():
    base = claim_streak_key("Verify Lemma E revision 7.2 in artifacts/proofs")
    assert base == claim_streak_key("Verify Lemma E revision 8 in artifacts/proofs")
    assert base == claim_streak_key("Verify Lemma E rev 9 in artifacts/proofs")
    assert base != claim_streak_key("Verify the squeeze invariant maintenance bound")


def test_claim_streak_key_keeps_distinct_numbered_claims_apart():
    assert claim_streak_key("Verify Lemma 3 in artifacts/proofs") != claim_streak_key("Verify Lemma 7 in artifacts/proofs")
    # A claim that is nothing but a revision marker still gets a non-empty key.
    assert claim_streak_key("revision 7") != ""


def test_run_state_is_not_running_for_dead_session(tmp_path, monkeypatch):
    project = _make_project(tmp_path)
    monkeypatch.setattr(workflow_command, "latest_goal_logs", lambda root, session: {"pane_log": None})
    state = workflow_command._run_state(project, session_name="iteris-dead", session_live=False)
    assert state == "not_active"

    pane = tmp_path / "pane.log"
    pane.write_text("some interrupted output\n", encoding="utf-8")
    monkeypatch.setattr(workflow_command, "latest_goal_logs", lambda root, session: {"pane_log": str(pane)})
    state = workflow_command._run_state(project, session_name="iteris-dead", session_live=False)
    assert state == "stopped"

    pane.write_text("...\nGoal achieved\n", encoding="utf-8")
    state = workflow_command._run_state(project, session_name="iteris-dead", session_live=False)
    assert state == "achieved"


def test_validate_project_facts_reports_fact_type_counts(tmp_path):
    project = _make_project(tmp_path)
    write_fact(project, fact_id="fact:p:one", source_task="task-a", claim_summary="A claim.", statement="S1.", fact_type="claim")
    write_fact(project, fact_id="fact:p:two", source_task="task-a", claim_summary="A blocker.", statement="S2.", fact_type="blocker")
    write_fact(project, fact_id="fact:p:three", source_task="task-a", claim_summary="Another claim.", statement="S3.", fact_type="claim")

    report = validate_project_facts(project, rebuild=False)
    assert report["ok"], report
    counts = report["fact_type_counts"]
    assert counts.get("claim") == 2
    assert counts.get("blocker") == 1

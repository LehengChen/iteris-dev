"""Crash consolidation command.

After a machine crash or hard kill, three kinds of state lie about reality:
agent-run status files still say running for dead worker pids, pool tasks stay
``running`` with nobody working on them, and the current-run record points at a
tmux session that no longer exists. ``iteris recover`` detects all three from
one liveness scan, repairs the first two, and prints the restart command.
"""

from __future__ import annotations

import json
from typing import Any

import typer

from iteris import log
from iteris.agents.runtime import drain_agent_run, write_status
from iteris.commands.common import require_public_project
from iteris.commands.workflow import default_session_name
from iteris.events import record_event
from iteris.liveness import scan_project_liveness
from iteris.project import now_iso
from iteris.tasks import repair_orphaned_task


def recover(
    project_path: str = typer.Argument(".", help="Iteris project path. Defaults to the current directory."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Report what would be repaired without writing."),
    json_output: bool = typer.Option(False, "--json", help="Print machine-readable JSON."),
) -> None:
    """Consolidate state after a crash: mark dead agent runs failed, reset orphaned tasks."""
    root = require_public_project(project_path)
    session_name = default_session_name(root)
    liveness = scan_project_liveness(root, session_name=session_name)
    actions: list[dict[str, Any]] = []

    for run in liveness["orphaned_agent_runs"]:
        if run.get("pid_alive"):
            # Worker still alive but its owning loop is gone: kill its codex
            # subtree + worker (it has no harvester), then mark it failed.
            action = {
                "action": "reap_orphaned_worker",
                "run_id": run["run_id"],
                "task_id": run.get("task_id"),
                "pid": run.get("pid"),
                "reason": run.get("reason"),
            }
            if not dry_run:
                drained = drain_agent_run(root, run["run_id"], reason="recovered: owning /goal loop is gone")
                action["drain"] = drained["actions"]
        else:
            action = {
                "action": "mark_agent_run_failed",
                "run_id": run["run_id"],
                "task_id": run.get("task_id"),
                "pid": run.get("pid"),
            }
            if not dry_run:
                write_status(
                    root / "artifacts" / "agent_runs" / run["run_id"],
                    {
                        "status": "failed",
                        "error": f"recovered: worker pid {run.get('pid')} is not running and left no terminal status",
                        "updated_at": now_iso(),
                    },
                )
        actions.append(action)

    for task in liveness["harvestable_pool_tasks"]:
        task_id = str(task.get("task_id") or "")
        if not task_id:
            continue
        assigned = task.get("assigned_agent_run")
        repaired: dict[str, Any] | None = {"placeholder": True}
        if not dry_run:
            repaired = repair_orphaned_task(
                root,
                task_id,
                to_status="review",
                note=(
                    f"iteris recover {now_iso()}: worker for {assigned} completed and left output "
                    "but the task was never harvested; moved to review."
                ),
                expected_assigned_run=assigned,
            )
        actions.append(
            {
                "action": "move_task_to_review" if repaired else "skip_task_changed_since_scan",
                "task_id": task_id,
                "assigned_agent_run": assigned,
            }
        )

    for task in liveness["orphaned_pool_tasks"]:
        task_id = str(task.get("task_id") or "")
        if not task_id:
            continue
        assigned = task.get("assigned_agent_run")
        detail = f"agent run {assigned} died without output" if assigned else "no live agent run or session"
        repaired = {"placeholder": True}
        if not dry_run:
            repaired = repair_orphaned_task(
                root,
                task_id,
                to_status="ready",
                note=f"iteris recover {now_iso()}: {detail}; task reset to ready.",
                expected_assigned_run=assigned,
                clear_assigned_run=True,
            )
        actions.append(
            {
                "action": "reset_task_to_ready" if repaired else "skip_task_changed_since_scan",
                "task_id": task_id,
                "assigned_agent_run": assigned,
            }
        )

    if not dry_run and actions:
        record_event(root, "project_recovered", {"session_name": session_name, "actions": actions})

    payload = {
        "schema_version": "iteris.recover.v0",
        "project_path": str(root),
        "session_name": session_name,
        "dry_run": dry_run,
        "session_live": liveness["session_live"],
        "run_record_stale": liveness["run_record_stale"],
        "live_agent_runs": liveness["live_agent_runs"],
        "actions": actions,
        "restart_hint": None if liveness["session_live"] else "iteris run",
    }
    if json_output:
        typer.echo(json.dumps(payload, indent=2, ensure_ascii=False))
        return
    if not actions and liveness["session_live"]:
        log.success("Nothing to recover: session is live and no orphaned state found.")
        return
    if actions:
        verb = "Would repair" if dry_run else "Repaired"
        rows = [
            (item["action"], str(item.get("run_id") or item.get("task_id") or ""), str(item.get("task_id") or ""))
            for item in actions
        ]
        log.results_table(rows, title=f"{verb} {len(actions)} item(s)")
    else:
        log.info("No orphaned agent runs or tasks found.")
    if liveness["live_agent_runs"]:
        live = ", ".join(str(item.get("task_id") or item["run_id"]) for item in liveness["live_agent_runs"])
        log.info(f"Left untouched (still running): {live}")
    if not liveness["session_live"]:
        log.info("Run session is not live. Restart with: iteris run")

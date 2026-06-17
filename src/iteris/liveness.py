"""Project liveness scanning.

Single source of truth for "what is actually alive in this project":
the tmux run session, detached agent-run workers, and pool tasks whose
assigned workers died. Used by `iteris status` and `iteris recover` so
the two never disagree.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from iteris.agents.runtime import pid_running
from iteris.project import read_json
from iteris.tasks import load_task_pool
from iteris.tmux import tmux_session_alive

# Statuses that mean "a worker process should currently exist for this run".
ACTIVE_AGENT_RUN_STATUSES = {"running", "pending"}

# A just-created run may sit at "pending" (or "running" without its pid write
# landing yet) for a moment between status writes. Runs younger than this are
# never reported orphaned, so a concurrent `iteris recover` cannot kill a
# launch in progress.
LAUNCH_GRACE_SECONDS = 60.0


def scan_agent_runs(
    project_root: Path,
    *,
    now: datetime | None = None,
    session_live: bool | None = None,
) -> dict[str, list[dict[str, Any]]]:
    """Classify agent runs whose recorded status claims they are active.

    Returns ``{"live": [...], "orphaned": [...]}``. A run is orphaned when its
    status file says running/pending but its worker pid is dead or missing and
    the run is older than the launch grace window. Completed/failed runs are
    not scanned; they have nothing to recover.

    When ``session_live`` is ``False``, a worker whose pid is still alive but
    whose owning ``/goal`` loop is gone is also orphaned (``pid_alive: True``):
    it has no harvester and keeps burning budget, so the reaper must kill it.
    Each entry carries ``exec_pgid`` so the reaper can also kill the executor
    subtree. ``session_live=None`` preserves the original pid-only behavior.
    """
    runs_dir = project_root / "artifacts" / "agent_runs"
    live: list[dict[str, Any]] = []
    orphaned: list[dict[str, Any]] = []
    if not runs_dir.exists():
        return {"live": live, "orphaned": orphaned}
    reference = now or datetime.now(timezone.utc)
    for run_dir in sorted(path for path in runs_dir.iterdir() if path.is_dir()):
        status = read_json(run_dir / "status.json", default=None)
        if not isinstance(status, dict):
            continue
        state = str(status.get("status") or "")
        if state not in ACTIVE_AGENT_RUN_STATUSES:
            continue
        request = read_json(run_dir / "request.json", default={})
        request = request if isinstance(request, dict) else {}
        pid = status.get("pid")
        entry = {
            "run_id": run_dir.name,
            "task_id": request.get("task_id"),
            "role": request.get("role"),
            "status": state,
            "pid": pid,
            "exec_pgid": status.get("exec_pgid") if status.get("exec_pgid") is not None else status.get("codex_pgid"),
            "status_path": str((run_dir / "status.json").relative_to(project_root)),
        }
        alive = isinstance(pid, int) and pid_running(pid)
        if alive and session_live is False:
            # Worker still running but its owning /goal loop is gone: an orphan
            # with no harvester, burning budget. Reap it (kill, don't just mark).
            orphaned.append({**entry, "pid_alive": True, "reason": "owning_loop_gone"})
        elif alive:
            live.append(entry)
        elif session_live is not False and _age_seconds(status.get("updated_at"), reference) < LAUNCH_GRACE_SECONDS:
            # Mid-launch: pid not recorded yet. Count as live, never orphaned.
            live.append(entry)
        else:
            orphaned.append({**entry, "pid_alive": False, "reason": "worker_pid_dead"})
    return {"live": live, "orphaned": orphaned}


def scan_project_liveness(project_root: Path, *, session_name: str) -> dict[str, Any]:
    """Full liveness picture for status/recover.

    Pool-task classification, by what the assigned agent run actually did:

    - assigned run live (or in launch grace) -> healthy, untouched
    - assigned run terminal ``completed``    -> ``harvestable_pool_tasks``
      (output exists; recover moves the task to ``review``, never resets it)
    - assigned run dead/failed/missing       -> ``orphaned_pool_tasks``
    - no assigned run at all                 -> orphaned only when the main
      session is dead too; an in-session task being worked on by the live
      main agent is healthy.
    """
    root = project_root.resolve()
    session_live = tmux_session_alive(session_name)
    run_record = read_json(root / ".iteris" / "current_run.json", default={})
    run_record = run_record if isinstance(run_record, dict) else {}
    recorded_session = str(run_record.get("session_name") or "")
    run_record_stale = bool(recorded_session) and not tmux_session_alive(recorded_session)
    agent_runs = scan_agent_runs(root, session_live=session_live)
    live_run_ids = {entry["run_id"] for entry in agent_runs["live"]}
    orphaned_tasks: list[dict[str, Any]] = []
    harvestable_tasks: list[dict[str, Any]] = []
    pool = load_task_pool(root)
    for task in pool.get("tasks", []):
        if not isinstance(task, dict) or task.get("status") != "running":
            continue
        assigned = str(task.get("assigned_agent_run") or "")
        entry = {
            "task_id": task.get("task_id"),
            "assigned_agent_run": assigned or None,
            "updated_at": task.get("updated_at"),
        }
        if assigned:
            if assigned in live_run_ids:
                continue
            run_status = read_json(root / "artifacts" / "agent_runs" / assigned / "status.json", default=None)
            run_state = str(run_status.get("status") or "") if isinstance(run_status, dict) else ""
            if run_state == "completed":
                harvestable_tasks.append(entry)
            else:
                orphaned_tasks.append(entry)
        elif not session_live:
            orphaned_tasks.append(entry)
    return {
        "schema_version": "iteris.liveness.v0",
        "session_name": session_name,
        "session_live": session_live,
        "run_record_session": recorded_session or None,
        "run_record_stale": run_record_stale,
        "live_agent_runs": agent_runs["live"],
        "orphaned_agent_runs": agent_runs["orphaned"],
        "orphaned_pool_tasks": orphaned_tasks,
        "harvestable_pool_tasks": harvestable_tasks,
        "needs_recovery": bool(agent_runs["orphaned"] or orphaned_tasks or harvestable_tasks),
    }


def _age_seconds(updated_at: Any, reference: datetime) -> float:
    """Age of a status timestamp; unparseable/missing timestamps count as old."""
    if not isinstance(updated_at, str) or not updated_at:
        return float("inf")
    try:
        parsed = datetime.fromisoformat(updated_at.replace("Z", "+00:00"))
    except ValueError:
        return float("inf")
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return (reference - parsed).total_seconds()

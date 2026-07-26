"""Family closure state, sibling phases, and joint scheduling."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

from iteris.project import is_project, now_iso, read_json, session_slug, write_json
from iteris.supervision.profiles.evolve import goal_success_verified, principled_stop_certified
from iteris.tasks import load_task_pool, select_ready_tasks
from iteris.tmux import tmux_session_alive, tmux_target

FAMILY_STATE_SCHEMA = "iteris.family_state.v1"
FAMILY_MEMBER_SCHEMA = "iteris.family_member.v0"
FAMILY_WATCHDOG_STATE_SCHEMA = "iteris.family_watchdog_state.v0"


class FamilyError(Exception):
    """Raised when family state is missing or invalid."""


def family_state_path(family_root: Path) -> Path:
    return family_root / ".iteris" / "FAMILY.json"


def family_marker_path(project_root: Path) -> Path:
    return project_root / ".iteris" / "family.json"


def family_watchdog_state_path(family_root: Path) -> Path:
    return family_root / ".iteris" / "family_watchdog_state.json"


def is_family_closure_root(path: Path) -> bool:
    return family_state_path(path.resolve()).is_file()


def read_family_state(family_root: Path) -> dict[str, Any]:
    payload = read_json(family_state_path(family_root), default=None)
    if not isinstance(payload, dict):
        raise FamilyError(f"missing family state: {family_state_path(family_root)}")
    return payload


def write_family_state(family_root: Path, state: dict[str, Any]) -> dict[str, Any]:
    payload = dict(state)
    payload["schema_version"] = FAMILY_STATE_SCHEMA
    payload["updated_at"] = now_iso()
    path = family_state_path(family_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    write_json(path, payload)
    return payload


def read_family_marker(project_root: Path) -> dict[str, Any] | None:
    payload = read_json(family_marker_path(project_root), default=None)
    return payload if isinstance(payload, dict) else None


def write_family_marker(project_root: Path, marker: dict[str, Any]) -> dict[str, Any]:
    payload = dict(marker)
    payload["schema_version"] = FAMILY_MEMBER_SCHEMA
    payload["updated_at"] = now_iso()
    path = family_marker_path(project_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    write_json(path, payload)
    return payload


def resolve_family_root_from_path(path: Path) -> Path | None:
    """Resolve a family closure wrapper root from a family root or sibling path."""
    root = path.resolve()
    if is_family_closure_root(root):
        return root
    marker = read_family_marker(root)
    if isinstance(marker, dict) and marker.get("family_root"):
        candidate = Path(str(marker["family_root"])).resolve()
        if is_family_closure_root(candidate):
            return candidate
    return None


def require_family_root(path: Path) -> Path:
    family_root = resolve_family_root_from_path(path)
    if family_root is None:
        raise FamilyError(f"not a family closure root or sibling: {path.resolve()}")
    return family_root


def sibling_project_path(family_root: Path, sibling: dict[str, Any]) -> Path:
    rel = sibling.get("path")
    if not rel:
        raise FamilyError(f"sibling {sibling.get('sibling_id')} missing path")
    project = (family_root / str(rel)).resolve()
    if not is_project(project):
        raise FamilyError(f"not an Iteris project: {project}")
    return project


def sibling_by_id(state: dict[str, Any], sibling_id: str) -> dict[str, Any]:
    for sibling in state.get("siblings") or []:
        if isinstance(sibling, dict) and str(sibling.get("sibling_id")) == sibling_id:
            return sibling
    raise FamilyError(f"unknown sibling id: {sibling_id}")


def sibling_session_name(family_root: Path, sibling: dict[str, Any]) -> str:
    if sibling.get("session"):
        return str(sibling["session"])
    project = sibling_project_path(family_root, sibling)
    return f"iteris-{session_slug(project.name)}"


def session_live(session_name: str) -> bool:
    try:
        return tmux_session_alive(session_name)
    except RuntimeError:
        return False


def _claim_prefix_for_sibling(state: dict[str, Any], sibling: dict[str, Any]) -> str:
    prefix = sibling.get("claim_prefix")
    if isinstance(prefix, str) and prefix.strip():
        return prefix.strip()
    policy = state.get("policy") or {}
    family_prefix = policy.get("claim_prefix")
    return str(family_prefix).strip() if family_prefix else ""


def _goal_success_claim(project: Path) -> str:
    results_dir = project / "verification" / "results"
    if not results_dir.exists():
        return ""
    for path in sorted(results_dir.glob("*.json")):
        payload = read_json(path, default=None)
        if not isinstance(payload, dict):
            continue
        if payload.get("mode") != "goal_success" or payload.get("passed") is not True:
            continue
        claim = str(payload.get("claim") or "").strip()
        if claim:
            return claim
    return ""


def claim_matches_prefix(claim: str, prefix: str) -> bool:
    if not prefix:
        return True
    claim = claim.strip()
    return claim.startswith(prefix) or prefix in claim


def sibling_goal_closed(project: Path, state: dict[str, Any], sibling: dict[str, Any]) -> bool:
    if not goal_success_verified(project):
        return False
    prefix = _claim_prefix_for_sibling(state, sibling)
    if not prefix:
        return True
    return claim_matches_prefix(_goal_success_claim(project), prefix)


def sibling_reduced(project: Path, state: dict[str, Any]) -> bool:
    policy = state.get("policy") or {}
    if not policy.get("allow_principled_stop"):
        return False
    return principled_stop_certified(project)


def _verified_blocker_present(project: Path) -> bool:
    index_path = project / "memory" / "facts" / "FACT_INDEX.jsonl"
    if not index_path.exists():
        return False
    for line in index_path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(row, dict) or row.get("status") != "verified":
            continue
        fact_type = str(row.get("fact_type") or "").lower()
        claim = str(row.get("claim_summary") or row.get("claim") or "").lower()
        if "blocker" in fact_type or "gap" in fact_type or "blocker" in claim or "impossibility" in claim:
            return True
    status_path = project / "STATUS.md"
    if status_path.exists():
        first = status_path.read_text(encoding="utf-8", errors="replace").splitlines()[:3]
        text = "\n".join(first).lower()
        if "phase: blocked" in text or "phase: blocked_partial" in text:
            return True
    return False


def sibling_phase(family_root: Path, sibling: dict[str, Any], state: dict[str, Any] | None = None) -> str:
    state = state or read_family_state(family_root)
    project = sibling_project_path(family_root, sibling)
    if sibling_goal_closed(project, state, sibling):
        return "closed"
    if sibling_reduced(project, state):
        return "reduced"
    if _verified_blocker_present(project):
        return "blocked_partial"
    return "open"


def _priority_key(sibling: dict[str, Any]) -> tuple[int, str]:
    try:
        priority = int(sibling.get("priority", 0))
    except (TypeError, ValueError):
        priority = 0
    return (priority, str(sibling.get("sibling_id") or ""))


def sibling_task_summary(project: Path) -> dict[str, Any]:
    pool = load_task_pool(project)
    ready = select_ready_tasks(project, limit=100)
    alignment_ready = [
        task
        for task in ready
        if isinstance(task, dict)
        and any(token in str(task.get("task_id") or "").lower() + str(task.get("title") or "").lower()
                for token in ("align", "fc-", "gap", "north-star", "northstar"))
    ]
    return {
        "active_frontier": pool.get("active_frontier") or "",
        "ready_count": len(ready),
        "alignment_ready_count": len(alignment_ready),
    }


def sibling_status_row(family_root: Path, sibling: dict[str, Any], state: dict[str, Any]) -> dict[str, Any]:
    project = sibling_project_path(family_root, sibling)
    session_name = sibling_session_name(family_root, sibling)
    phase = sibling_phase(family_root, sibling, state)
    tasks = sibling_task_summary(project)
    return {
        "sibling_id": sibling.get("sibling_id"),
        "path": sibling.get("path"),
        "project": str(project),
        "phase": phase,
        "session": session_name,
        "session_live": session_live(session_name),
        "target_artifact": sibling.get("target_artifact"),
        "gaps": sibling.get("gaps") or [],
        "active_frontier": tasks["active_frontier"],
        "ready_tasks": tasks["ready_count"],
        "alignment_ready_tasks": tasks["alignment_ready_count"],
    }


def family_status(family_root: Path) -> dict[str, Any]:
    family_root = family_root.resolve()
    state = read_family_state(family_root)
    siblings = [
        sibling_status_row(family_root, sibling, state)
        for sibling in state.get("siblings") or []
        if isinstance(sibling, dict)
    ]
    running = sum(1 for row in siblings if row["phase"] == "open" and row["session_live"])
    return {
        "family_root": str(family_root),
        "goal": state.get("goal"),
        "schedule": state.get("schedule") or {},
        "policy": state.get("policy") or {},
        "run": state.get("run") or {},
        "siblings": siblings,
        "running_open": running,
        "max_concurrent": int((state.get("schedule") or {}).get("max_concurrent") or 1),
    }


def watchdog_session_name(family_root: Path, state: dict[str, Any] | None = None) -> str:
    state = state or read_family_state(family_root)
    run = state.get("run") or {}
    if run.get("watchdog_session"):
        return str(run["watchdog_session"])
    return f"iteris-{session_slug(family_root.name)}-watchdog"


def start_sibling_run(
    family_root: Path,
    sibling: dict[str, Any],
    state: dict[str, Any],
    *,
    dry_run: bool = False,
) -> dict[str, Any]:
    project = sibling_project_path(family_root, sibling)
    session_name = sibling_session_name(family_root, sibling)
    policy = state.get("policy") or {}
    goal: str | None = None
    goal_path = project / ".iteris" / "watchdog_goal.txt"
    if policy.get("prefer_watchdog_goal", True) and goal_path.is_file():
        text = goal_path.read_text(encoding="utf-8", errors="replace").strip()
        if text:
            goal = text
    cmd = [
        sys.executable,
        "-m",
        "iteris.cli",
        "run",
        str(project),
        "--session",
        session_name,
        "--new-session",
    ]
    if goal:
        cmd.extend(["--goal", goal])
    target = sibling.get("target_artifact")
    if target:
        cmd.extend(["--target-artifact", str(target)])
    payload: dict[str, Any] = {
        "action": "start",
        "sibling_id": sibling.get("sibling_id"),
        "project": str(project),
        "session": session_name,
        "command": cmd,
    }
    if dry_run:
        payload["dry_run"] = True
        return payload
    proc = subprocess.run(cmd, capture_output=True, text=True)
    payload["exit_code"] = proc.returncode
    payload["stdout"] = proc.stdout[-4000:]
    payload["stderr"] = proc.stderr[-4000:]
    payload["ok"] = proc.returncode == 0
    return payload


def schedule_actions(
    family_root: Path,
    state: dict[str, Any] | None = None,
    *,
    dry_run: bool = False,
) -> list[dict[str, Any]]:
    family_root = family_root.resolve()
    state = state or read_family_state(family_root)
    schedule = state.get("schedule") or {}
    try:
        max_concurrent = int(schedule.get("max_concurrent") or 1)
    except (TypeError, ValueError):
        max_concurrent = 1
    max_concurrent = max(1, max_concurrent)

    open_siblings = [
        sibling
        for sibling in state.get("siblings") or []
        if isinstance(sibling, dict) and sibling_phase(family_root, sibling, state) == "open"
    ]
    running = sum(
        1
        for sibling in open_siblings
        if session_live(sibling_session_name(family_root, sibling))
    )
    slots = max(0, max_concurrent - running)
    candidates = [
        sibling
        for sibling in open_siblings
        if not session_live(sibling_session_name(family_root, sibling))
    ]
    candidates.sort(key=_priority_key)
    actions: list[dict[str, Any]] = []
    for sibling in candidates[:slots]:
        actions.append(start_sibling_run(family_root, sibling, state, dry_run=dry_run))
    return actions


def stop_sibling_session(family_root: Path, sibling: dict[str, Any]) -> dict[str, Any]:
    session_name = sibling_session_name(family_root, sibling)
    stopped = False
    if session_live(session_name):
        subprocess.run(["tmux", "kill-session", "-t", tmux_target(session_name)], check=False)
        stopped = True
    return {"sibling_id": sibling.get("sibling_id"), "session": session_name, "stopped": stopped}


def stop_watchdog_session(family_root: Path, state: dict[str, Any] | None = None) -> dict[str, Any]:
    state = state or read_family_state(family_root)
    session_name = watchdog_session_name(family_root, state)
    stopped = False
    if session_live(session_name):
        subprocess.run(["tmux", "kill-session", "-t", tmux_target(session_name)], check=False)
        stopped = True
    return {"session": session_name, "stopped": stopped}


def touch_watchdog_tick(family_root: Path) -> dict[str, Any]:
    payload = {
        "schema_version": FAMILY_WATCHDOG_STATE_SCHEMA,
        "last_tick_at": now_iso(),
    }
    path = family_watchdog_state_path(family_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    write_json(path, payload)
    state = read_family_state(family_root)
    run = dict(state.get("run") or {})
    run["last_tick_at"] = payload["last_tick_at"]
    state["run"] = run
    write_family_state(family_root, state)
    return payload

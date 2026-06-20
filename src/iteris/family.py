"""Family closure state: parallel sibling north-star tracks under one operator.

``.iteris/FAMILY.json`` at the family wrapper root is the single source of truth
for joint scheduling. Siblings are existing Iteris projects (often symlinks to
evolve repos). This is distinct from ``iteris evolve``, which generalizes from
one verified result; family closure coordinates related quantifier tracks.

Vocabulary: **sibling** (not evolve *node*), **alignment** (not *direction*).
Project-internal work still lives in each sibling's TASK_POOL.json frontier.
"""

from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from iteris.project import is_project, now_iso, project_id_from_path, read_json, session_slug, slugify, write_json
from iteris.tasks import load_task_pool, normalize_task_status

FAMILY_SCHEMA = "iteris.family_state.v1"
FAMILY_MARKER_SCHEMA = "iteris.family_member.v0"

DEFAULT_SCHEDULE = {
    "max_concurrent": 2,
    "stall_hours": 18.0,
}

DEFAULT_POLICY = {
    "prefer_watchdog_goal": True,
    "allow_principled_stop": False,
    "claim_prefix": None,
}


class FamilyError(RuntimeError):
    """Raised for user-facing family state errors."""


def resolve_family_root(path: str | Path) -> Path:
    root = Path(path).resolve()
    if not root.is_dir():
        raise FamilyError(f"not a directory: {root}")
    return root


def family_path(root: Path) -> Path:
    return root / ".iteris" / "FAMILY.json"


def has_family_state(root: Path) -> bool:
    return family_path(root).exists()


def read_state(root: Path) -> dict[str, Any]:
    state = read_json(family_path(root), default=None)
    if not isinstance(state, dict):
        raise FamilyError(f"no family state at {family_path(root)}; run `iteris family init` first")
    return state


def write_state(root: Path, state: dict[str, Any]) -> Path:
    state["updated_at"] = now_iso()
    path = family_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    write_json(path, state)
    return path


def sibling_marker_path(project_root: Path) -> Path:
    return project_root / ".iteris" / "family.json"


def family_root_entry(family_root: Path) -> dict[str, Any]:
    return {"path": str(family_root.resolve()), "family_id": project_id_from_path(family_root)}


def write_sibling_marker(sibling_root: Path, family_root: Path, sibling_id: str) -> None:
    payload = {
        "schema_version": FAMILY_MARKER_SCHEMA,
        "family_root": str(family_root.resolve()),
        "family_id": project_id_from_path(family_root),
        "sibling_id": sibling_id,
        "updated_at": now_iso(),
    }
    path = sibling_marker_path(sibling_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    write_json(path, payload)


def read_family_marker(project_root: Path) -> dict[str, Any] | None:
    payload = read_json(sibling_marker_path(project_root), default=None)
    return payload if isinstance(payload, dict) else None


def init_state(
    root: Path,
    *,
    goal: str,
    siblings: list[dict[str, Any]] | None = None,
    schedule: dict[str, Any] | None = None,
    policy: dict[str, Any] | None = None,
    adopt_symlinks: bool = True,
) -> dict[str, Any]:
    root = resolve_family_root(root)
    if has_family_state(root):
        raise FamilyError(f"family state already exists: {family_path(root)}")
    resolved_siblings = siblings or []
    if adopt_symlinks and not resolved_siblings:
        resolved_siblings = _adopt_symlink_siblings(root)
    if not resolved_siblings:
        raise FamilyError("no siblings defined; pass --sibling or place iteris-project symlinks under the family root")
    _validate_siblings(root, resolved_siblings)
    state = {
        "schema_version": FAMILY_SCHEMA,
        "goal": goal.strip(),
        "created_at": now_iso(),
        "schedule": {**DEFAULT_SCHEDULE, **(schedule or {})},
        "policy": {**DEFAULT_POLICY, **(policy or {})},
        "run": {"started_at": None},
        "siblings": resolved_siblings,
        "pool": {
            "index": "memory/family/FAMILY_INDEX.jsonl",
            "export_command": "iteris family export",
        },
    }
    write_state(root, state)
    for entry in resolved_siblings:
        sibling_root = resolve_sibling_path(root, entry)
        write_sibling_marker(sibling_root, root, entry["sibling_id"])
    (root / "memory" / "family").mkdir(parents=True, exist_ok=True)
    return state


def _adopt_symlink_siblings(family_root: Path) -> list[dict[str, Any]]:
    adopted: list[dict[str, Any]] = []
    seen: set[str] = set()
    for path in sorted(family_root.iterdir()):
        if path.name.startswith("."):
            continue
        if not path.is_dir():
            continue
        if not (path.is_symlink() or is_project(path)):
            continue
        if path.name in seen:
            continue
        seen.add(path.name)
        adopted.append(
            {
                "sibling_id": path.name,
                "path": path.name,
                "session": f"iteris-{session_slug(path.name)}",
            }
        )
    return adopted


def _validate_siblings(family_root: Path, siblings: list[dict[str, Any]]) -> None:
    seen: set[str] = set()
    for entry in siblings:
        sid = str(entry.get("sibling_id") or "").strip()
        if not sid:
            raise FamilyError("each sibling needs sibling_id")
        if sid in seen:
            raise FamilyError(f"duplicate sibling_id: {sid}")
        seen.add(sid)
        resolve_sibling_path(family_root, entry)


def resolve_sibling_path(family_root: Path, sibling: dict[str, Any]) -> Path:
    rel = str(sibling.get("path") or sibling.get("sibling_id") or "").strip()
    if not rel:
        raise FamilyError("sibling entry missing path")
    candidate = (family_root / rel).resolve()
    if is_project(candidate):
        return candidate
    evolve_dir = sibling.get("evolve_dir")
    if evolve_dir:
        alt = (family_root.parent / str(evolve_dir)).resolve()
        if is_project(alt):
            return alt
    raise FamilyError(f"cannot resolve sibling {sibling.get('sibling_id')!r} -> {rel}")


def read_watchdog_goal(sibling_root: Path) -> str | None:
    path = sibling_root / ".iteris" / "watchdog_goal.txt"
    if not path.exists():
        return None
    text = path.read_text(encoding="utf-8", errors="replace").strip()
    return text or None


def goal_success_verified(project: Path) -> bool:
    """Mechanical goal_success check; prefer index tail over full results scan."""
    index_path = project / "verification" / "VERIFICATION_INDEX.jsonl"
    if index_path.exists():
        try:
            lines = index_path.read_text(encoding="utf-8", errors="replace").splitlines()
            for line in reversed(lines[-200:]):
                if not line.strip():
                    continue
                payload = json.loads(line)
                if not isinstance(payload, dict):
                    continue
                if payload.get("mode") != "goal_success" or payload.get("passed") is not True:
                    continue
                target = payload.get("target_artifact")
                if not target or (project / str(target)).exists():
                    return True
        except (json.JSONDecodeError, OSError):
            pass
    results_dir = project / "verification" / "results"
    if not results_dir.exists():
        return False
    for path in sorted(results_dir.glob("*.json"), reverse=True)[:50]:
        payload = read_json(path, default=None)
        if not isinstance(payload, dict):
            continue
        if payload.get("mode") != "goal_success" or payload.get("passed") is not True:
            continue
        target = payload.get("target_artifact")
        if not target or (project / str(target)).exists():
            return True
    return False


def principled_stop_certified(project: Path) -> bool:
    index_path = project / "verification" / "VERIFICATION_INDEX.jsonl"
    if index_path.exists():
        try:
            lines = index_path.read_text(encoding="utf-8", errors="replace").splitlines()
            for line in reversed(lines[-200:]):
                if not line.strip():
                    continue
                payload = json.loads(line)
                if not isinstance(payload, dict):
                    continue
                if payload.get("mode") != "principled_stop" or payload.get("passed") is not True:
                    continue
                target = payload.get("target_artifact")
                if not target or (project / str(target)).exists():
                    return True
        except (json.JSONDecodeError, OSError):
            pass
    results_dir = project / "verification" / "results"
    if not results_dir.exists():
        return False
    for path in sorted(results_dir.glob("*.json"), reverse=True)[:50]:
        payload = read_json(path, default=None)
        if not isinstance(payload, dict):
            continue
        if payload.get("mode") != "principled_stop" or payload.get("passed") is not True:
            continue
        target = payload.get("target_artifact")
        if not target or (project / str(target)).exists():
            return True
    return False


def _pool_summary(project: Path) -> dict[str, Any]:
    pool = load_task_pool(project)
    counts: dict[str, int] = {}
    ready_align: list[str] = []
    for task in pool.get("tasks", []):
        if not isinstance(task, dict):
            continue
        status = normalize_task_status(str(task.get("status") or ""))
        counts[status] = counts.get(status, 0) + 1
        if status == "ready" and str(task.get("task_id", "")).startswith("task-align-"):
            ready_align.append(str(task["task_id"]))
    ready_align.sort(key=lambda tid: -next(
        (int(t.get("priority") or 0) for t in pool.get("tasks", []) if t.get("task_id") == tid), 0
    ))
    return {
        "active_frontier": pool.get("active_frontier") or "",
        "counts": counts,
        "ready_alignment_tasks": ready_align[:5],
    }


def _session_live(session_name: str) -> bool:
    try:
        from iteris.tmux import tmux_session_alive

        return tmux_session_alive(session_name)
    except Exception:
        return False


def sibling_phase(project: Path, *, allow_principled_stop: bool) -> str:
    if goal_success_verified(project):
        return "closed"
    if principled_stop_certified(project):
        return "reduced" if allow_principled_stop else "blocked_partial"
    return "open"


def sibling_snapshot(family_root: Path, entry: dict[str, Any], *, policy: dict[str, Any]) -> dict[str, Any]:
    sibling_root = resolve_sibling_path(family_root, entry)
    session = str(entry.get("session") or f"iteris-{session_slug(sibling_root.name)}")
    allow_ps = bool(entry.get("allow_principled_stop", policy.get("allow_principled_stop")))
    phase = sibling_phase(sibling_root, allow_principled_stop=allow_ps)
    live = _session_live(session)
    pool = _pool_summary(sibling_root)
    watchdog = read_watchdog_goal(sibling_root) if policy.get("prefer_watchdog_goal", True) else None
    return {
        "sibling_id": entry.get("sibling_id"),
        "path": str(sibling_root),
        "session": session,
        "session_live": live,
        "phase": phase,
        "gaps": entry.get("gaps") or [],
        "target_artifact": entry.get("target_artifact"),
        "claim_prefix": entry.get("claim_prefix") or policy.get("claim_prefix"),
        "pool": pool,
        "has_watchdog_goal": bool(watchdog),
        "priority": int(entry.get("priority") or 0),
    }


def family_status(family_root: Path) -> dict[str, Any]:
    root = resolve_family_root(family_root)
    state = read_state(root)
    policy = state.get("policy") or {}
    schedule = state.get("schedule") or {}
    siblings = []
    for entry in state.get("siblings") or []:
        if isinstance(entry, dict):
            siblings.append(sibling_snapshot(root, entry, policy=policy))
    running = sum(1 for s in siblings if s["session_live"] and s["phase"] == "open")
    closed = sum(1 for s in siblings if s["phase"] == "closed")
    return {
        "family_id": project_id_from_path(root),
        "goal": state.get("goal"),
        "path": str(root),
        "schedule": schedule,
        "policy": policy,
        "siblings": siblings,
        "summary": {
            "total": len(siblings),
            "closed": closed,
            "running": running,
            "open": sum(1 for s in siblings if s["phase"] == "open"),
            "slots_available": max(0, int(schedule.get("max_concurrent") or DEFAULT_SCHEDULE["max_concurrent"]) - running),
        },
        "run": state.get("run") or {},
    }


def _priority_key(entry: dict[str, Any], snapshot: dict[str, Any]) -> tuple[int, str]:
    # Lower phase urgency first: open before closed; higher sibling priority first.
    phase_rank = {"open": 0, "blocked_partial": 1, "reduced": 2, "closed": 3}.get(snapshot["phase"], 9)
    return (phase_rank, -int(snapshot.get("priority") or 0), str(entry.get("sibling_id") or ""))


def schedule_actions(family_root: Path) -> list[dict[str, Any]]:
    """Return start actions for siblings that should be running but are not."""
    root = resolve_family_root(family_root)
    state = read_state(root)
    policy = state.get("policy") or {}
    schedule = {**DEFAULT_SCHEDULE, **(state.get("schedule") or {})}
    max_concurrent = int(schedule.get("max_concurrent") or DEFAULT_SCHEDULE["max_concurrent"])
    snapshots: list[tuple[dict[str, Any], dict[str, Any]]] = []
    for entry in state.get("siblings") or []:
        if not isinstance(entry, dict):
            continue
        snap = sibling_snapshot(root, entry, policy=policy)
        snapshots.append((entry, snap))
    running = sum(1 for _, snap in snapshots if snap["session_live"] and snap["phase"] == "open")
    slots = max(0, max_concurrent - running)
    actions: list[dict[str, Any]] = []
    candidates = [
        (entry, snap)
        for entry, snap in snapshots
        if snap["phase"] == "open" and not snap["session_live"]
    ]
    candidates.sort(key=lambda pair: _priority_key(pair[0], pair[1]))
    for entry, snap in candidates[:slots]:
        sibling_root = resolve_sibling_path(root, entry)
        goal = None
        if policy.get("prefer_watchdog_goal", True):
            goal = read_watchdog_goal(sibling_root)
        actions.append(
            {
                "action": "start_run",
                "sibling_id": entry.get("sibling_id"),
                "project_path": str(sibling_root),
                "session": snap["session"],
                "target_artifact": entry.get("target_artifact"),
                "goal": goal,
            }
        )
    return actions


def run_cli(args: list[str], *, cwd: Path | None = None) -> dict[str, Any]:
    proc = subprocess.run(
        [sys.executable, "-m", "iteris.cli", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        timeout=600,
    )
    payload: dict[str, Any] = {"returncode": proc.returncode, "stdout": proc.stdout, "stderr": proc.stderr}
    text = proc.stdout.strip()
    if text:
        try:
            payload["json"] = json.loads(text.splitlines()[-1])
        except json.JSONDecodeError:
            pass
    return payload


def start_sibling_run(action: dict[str, Any], *, dry_run: bool = False) -> dict[str, Any]:
    args = [
        "run",
        str(action["project_path"]),
        "--session",
        str(action["session"]),
        "--new-session",
        "--json",
    ]
    if action.get("target_artifact"):
        args.extend(["--target-artifact", str(action["target_artifact"])])
    if action.get("goal"):
        args.extend(["--goal", str(action["goal"])])
    if dry_run:
        return {"dry_run": True, "command": ["iteris", *args]}
    result = run_cli(args, cwd=Path(action["project_path"]))
    return result


def family_session_name(family_root: Path) -> str:
    return f"iteris-family-{session_slug(family_root.name)}"


def schedule_tick(family_root: Path, *, dry_run: bool = False) -> dict[str, Any]:
    actions = schedule_actions(family_root)
    outcomes = []
    for action in actions:
        outcomes.append(start_sibling_run(action, dry_run=dry_run))
    if not dry_run and actions:
        root = resolve_family_root(family_root)
        state = read_state(root)
        state.setdefault("run", {})["last_tick_at"] = now_iso()
        write_state(root, state)
    return {"actions": actions, "outcomes": outcomes, "dry_run": dry_run}

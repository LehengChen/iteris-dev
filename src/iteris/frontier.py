"""Project frontier map helpers.

Frontiers are route-level indexes over durable facts. Tasks and artifacts are
attached as supporting evidence, but facts remain the primary memory unit.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from iteris.artifacts import read_artifact_index
from iteris.memory.search import load_jsonl
from iteris.project import now_iso, project_id_from_path, read_json, slugify, write_json
from iteris.tasks import load_task_pool

FRONTIER_PATH = "memory/facts/FRONTIER_INDEX.json"
LIST_FIELDS = [
    "active_frontiers",
    "reviewed_positive_routes",
    "closed_lanes",
    "submitted_gates",
    "completion_gaps",
    "do_not_schedule_patterns",
]
UNASSIGNED_FRONTIER_ID = "auto-unassigned-route"
REFRESH_GENERATOR = "frontier_refresh"
GLOBAL_EXPLORE_BLOCKED_FRONTIER_THRESHOLD = 8
GLOBAL_EXPLORE_BLOCKER_THRESHOLD = 12
BLOCKER_TERMS = {
    "blocker",
    "blocked",
    "obstruction",
    "obstruct",
    "cannot",
    "fails",
    "failed",
    "failure",
    "gap",
    "missing",
    "refute",
    "refuted",
    "reject",
    "rejected",
    "insufficient",
    "counterexample",
}
ROUTE_STOPWORDS = {
    "a",
    "an",
    "and",
    "audit",
    "candidate",
    "certificate",
    "claim",
    "completed",
    "experiment",
    "fact",
    "foundation",
    "for",
    "from",
    "lemma",
    "proof",
    "prove",
    "route",
    "source",
    "task",
    "the",
    "theorem",
    "verify",
    "verified",
}


def default_frontier_index(project_root: Path) -> dict[str, Any]:
    return {
        "schema_version": "iteris.frontier_index.v0",
        "project_id": project_id_from_path(project_root),
        "updated_at": now_iso(),
        **{field: [] for field in LIST_FIELDS},
    }


def load_frontier_index(project_root: Path) -> dict[str, Any]:
    payload = read_json(project_root / FRONTIER_PATH, default={})
    if not isinstance(payload, dict):
        payload = {}
    base = default_frontier_index(project_root)
    base.update(payload)
    for field in LIST_FIELDS:
        if not isinstance(base.get(field), list):
            base[field] = []
    return base


def save_frontier_index(project_root: Path, payload: dict[str, Any]) -> dict[str, Any]:
    out = load_frontier_index(project_root)
    out.update(payload)
    out["schema_version"] = "iteris.frontier_index.v0"
    out["project_id"] = out.get("project_id") or project_id_from_path(project_root)
    out["updated_at"] = now_iso()
    write_json(project_root / FRONTIER_PATH, out)
    return out


def validate_frontier_index(project_root: Path) -> dict[str, Any]:
    payload = load_frontier_index(project_root)
    errors: list[dict[str, str]] = []
    warnings: list[dict[str, str]] = []
    if payload.get("schema_version") != "iteris.frontier_index.v0":
        errors.append({"location": FRONTIER_PATH, "issue": "invalid schema_version"})
    for field in LIST_FIELDS:
        if not isinstance(payload.get(field), list):
            errors.append({"location": field, "issue": "must be a list"})
    for field in ["active_frontiers", "closed_lanes", "completion_gaps"]:
        for index, item in enumerate(payload.get(field) or []):
            if not isinstance(item, dict):
                warnings.append({"location": f"{field}[{index}]", "issue": "entry should be an object"})
            elif not (item.get("frontier_id") or item.get("lane_id") or item.get("gap_id")):
                warnings.append({"location": f"{field}[{index}]", "issue": "entry should have a stable id"})
    return {
        "schema_version": "iteris.frontier_validation.v0",
        "ok": not errors,
        "errors": errors,
        "warnings": warnings,
        "path": FRONTIER_PATH,
        "summary": frontier_summary(payload),
    }


def frontier_summary(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "active_frontiers": len(payload.get("active_frontiers") or []),
        "reviewed_positive_routes": len(payload.get("reviewed_positive_routes") or []),
        "closed_lanes": len(payload.get("closed_lanes") or []),
        "submitted_gates": len(payload.get("submitted_gates") or []),
        "completion_gaps": len(payload.get("completion_gaps") or []),
        "do_not_schedule_patterns": len(payload.get("do_not_schedule_patterns") or []),
        "updated_at": payload.get("updated_at"),
    }


def set_active_frontier(
    project_root: Path,
    *,
    frontier_id: str,
    title: str,
    summary: str,
    tasks: list[str] | None = None,
    facts: list[str] | None = None,
    gaps: list[str] | None = None,
) -> dict[str, Any]:
    payload = load_frontier_index(project_root)
    entry = {
        "frontier_id": frontier_id,
        "title": title,
        "summary": summary,
        "tasks": tasks or [],
        "facts": facts or [],
        "completion_gaps": gaps or [],
        "updated_at": now_iso(),
    }
    payload["active_frontiers"] = [
        item for item in payload.get("active_frontiers", []) if not (isinstance(item, dict) and item.get("frontier_id") == frontier_id)
    ]
    payload["active_frontiers"].append(entry)
    return save_frontier_index(project_root, payload)


def refresh_frontier_from_project(project_root: Path) -> dict[str, Any]:
    """Refresh fact-centered frontier entries from project state."""

    payload = load_frontier_index(project_root)
    pool = load_task_pool(project_root)
    tasks = [task for task in pool.get("tasks", []) if isinstance(task, dict)]
    facts = load_jsonl(project_root / "memory" / "facts" / "FACT_INDEX.jsonl")
    artifacts = read_artifact_index(project_root)

    preserved = [
        item
        for item in payload.get("active_frontiers", [])
        if isinstance(item, dict) and item.get("generated_by") != REFRESH_GENERATOR
    ]
    entries: dict[str, dict[str, Any]] = {
        str(item.get("frontier_id")): _normalize_frontier_entry(item)
        for item in preserved
        if item.get("frontier_id")
    }
    fact_to_frontier = {
        str(fact_id): frontier_id
        for frontier_id, entry in entries.items()
        for fact_id in entry.get("fact_ids", [])
        if fact_id
    }
    task_to_frontier = {
        str(task_id): frontier_id
        for frontier_id, entry in entries.items()
        for task_id in entry.get("task_ids", [])
        if task_id
    }

    for fact in facts:
        fact_id = str(fact.get("fact_id") or "")
        if not fact_id:
            continue
        # Inherited boundary facts are advisory route knowledge, already
        # represented in do_not_schedule_patterns. Folding them into route
        # entries would flood blocker counts and trip the explore trigger on
        # the very first refresh of a freshly seeded project.
        if str(fact.get("claim_policy") or "") == "inherited_boundary_advisory":
            continue
        entry = entries.get(fact_to_frontier.get(fact_id, "")) or _entry_for(entries, _route_key_for_fact(fact))
        _add_unique(entry["fact_ids"], fact_id)
        if _is_blocker_fact(fact):
            _add_unique(entry["blocker_fact_ids"], fact_id)
        source_task = str(fact.get("source_task") or "")
        if source_task:
            _add_unique(entry["task_ids"], source_task)
        verification = fact.get("verification")
        if verification:
            _add_unique(entry["verification_ids"], str(verification))
        entry["evidence_terms"].extend(_terms_from_text(str(fact.get("claim_summary") or ""))[:8])
        entry["recent_fact_summaries"].append(
            {
                "fact_id": fact_id,
                "status": fact.get("status"),
                "claim_summary": fact.get("claim_summary"),
            }
        )

    for task in tasks:
        task_id = str(task.get("task_id") or "")
        if not task_id:
            continue
        entry = entries.get(task_to_frontier.get(task_id, "")) or _entry_for(entries, _route_key_for_task(task))
        _add_unique(entry["task_ids"], task_id)
        status = str(task.get("status") or "unknown")
        counts = entry.setdefault("task_status_counts", {})
        counts[status] = int(counts.get(status, 0)) + 1
        if status == "blocked":
            _add_unique(entry["blocked_tasks"], task_id)
        if status in {"ready", "running", "review"}:
            _add_unique(entry["active_tasks"], task_id)
        objective = str(task.get("objective") or "")
        if objective:
            entry["open_questions"].append(_truncate(objective, 220))

    for record in artifacts:
        run_id = str(record.get("run_id") or "")
        task_id = str(record.get("task_id") or "")
        entry = entries.get(task_to_frontier.get(task_id, "")) or _entry_for(entries, _route_key_for_artifact(record))
        if run_id:
            _add_unique(entry["agent_run_ids"], run_id)
        if task_id:
            _add_unique(entry["task_ids"], task_id)
        for path in record.get("created_artifacts") or []:
            _add_unique(entry["artifact_paths"], str(path))
        for fact in record.get("candidate_facts") or []:
            if isinstance(fact, dict) and fact.get("fact_id"):
                _add_unique(entry["fact_ids"], str(fact["fact_id"]))
        for request_id in record.get("verification_requests") or []:
            _add_unique(entry["verification_ids"], str(request_id))
        if record.get("role") == "explore":
            entry["last_explore_at"] = _max_iso(entry.get("last_explore_at"), str(record.get("created_at") or ""))
            entry["explore_run_count"] = int(entry.get("explore_run_count") or 0) + 1

    refreshed = [_finalize_entry(entry) for entry in entries.values()]
    refreshed.sort(key=_frontier_sort_key)
    payload["active_frontiers"] = refreshed
    payload["unassigned_route_evidence"] = [
        item for item in refreshed if item.get("frontier_id") == UNASSIGNED_FRONTIER_ID
    ]
    return save_frontier_index(project_root, payload)


def frontier_health(project_root: Path) -> dict[str, Any]:
    """Return a read-only route-health report for deciding whether to explore."""

    payload = load_frontier_index(project_root)
    frontiers = [item for item in payload.get("active_frontiers", []) if isinstance(item, dict)]
    if not frontiers:
        return {
            "schema_version": "iteris.frontier_health.v0",
            "ok": True,
            "explore_recommended": False,
            "needs_refresh": True,
            "reason": "FRONTIER_INDEX.json has no active frontiers; run `iteris tool frontier refresh . --json` first.",
            "recommended_focus": "",
            "frontiers": [],
        }

    reports = [_health_for_entry(entry) for entry in frontiers]
    recommended = [item for item in reports if item.get("explore_recommended")]
    global_report = None if recommended or _task_pool_has_active_work(project_root) else _global_health_report(reports)
    selected = recommended[0] if recommended else global_report or max(reports, key=lambda item: int(item.get("active_task_count") or 0))
    return {
        "schema_version": "iteris.frontier_health.v0",
        "ok": True,
        "explore_recommended": bool(recommended or global_report),
        "needs_refresh": False,
        "reason": selected.get("reason") or "No frontier currently meets the explore trigger.",
        "recommended_focus": selected.get("recommended_focus") if recommended or global_report else "",
        "frontiers": [*reports, *([global_report] if global_report else [])],
        "updated_at": payload.get("updated_at"),
    }


def _normalize_frontier_entry(item: dict[str, Any]) -> dict[str, Any]:
    entry = {
        "schema_version": "iteris.frontier_entry.v0",
        "frontier_id": str(item.get("frontier_id") or UNASSIGNED_FRONTIER_ID),
        "title": str(item.get("title") or "Unassigned route evidence"),
        "hypothesis": str(item.get("hypothesis") or item.get("summary") or ""),
        "summary": str(item.get("summary") or item.get("hypothesis") or ""),
        "status": str(item.get("status") or "active"),
        "fact_ids": list(item.get("fact_ids") or item.get("facts") or []),
        "blocker_fact_ids": list(item.get("blocker_fact_ids") or []),
        "task_ids": list(item.get("task_ids") or item.get("tasks") or []),
        "active_tasks": list(item.get("active_tasks") or []),
        "blocked_tasks": list(item.get("blocked_tasks") or []),
        "artifact_paths": list(item.get("artifact_paths") or []),
        "verification_ids": list(item.get("verification_ids") or []),
        "agent_run_ids": list(item.get("agent_run_ids") or []),
        "open_questions": list(item.get("open_questions") or item.get("completion_gaps") or []),
        "next_actions": list(item.get("next_actions") or []),
        "recent_fact_summaries": list(item.get("recent_fact_summaries") or []),
        "task_status_counts": dict(item.get("task_status_counts") or {}),
        "evidence_terms": list(item.get("evidence_terms") or []),
        "explore_run_count": int(item.get("explore_run_count") or 0),
        "last_explore_at": item.get("last_explore_at"),
        "generated_by": item.get("generated_by"),
        "updated_at": now_iso(),
    }
    return entry


def _entry_for(entries: dict[str, dict[str, Any]], route_key: str) -> dict[str, Any]:
    frontier_id = f"auto-{route_key}" if route_key else UNASSIGNED_FRONTIER_ID
    if frontier_id not in entries:
        title = " ".join(part.upper() if part.startswith("s") and part[1:].isdigit() else part for part in route_key.split("-")).strip()
        entries[frontier_id] = _normalize_frontier_entry(
            {
                "frontier_id": frontier_id,
                "title": title.title() if title else "Unassigned route evidence",
                "hypothesis": f"Route inferred from facts, tasks, and artifacts matching `{route_key or 'unassigned'}`.",
                "summary": "",
                "generated_by": REFRESH_GENERATOR,
            }
        )
    return entries[frontier_id]


def _route_key_for_fact(fact: dict[str, Any]) -> str:
    source_task = str(fact.get("source_task") or "")
    claim = str(fact.get("claim_summary") or "")
    return _route_key(f"{source_task} {claim}")


def _route_key_for_task(task: dict[str, Any]) -> str:
    return _route_key(f"{task.get('task_id') or ''} {task.get('objective') or ''}")


def _route_key_for_artifact(record: dict[str, Any]) -> str:
    task_id = str(record.get("task_id") or "")
    focus = str(record.get("focus") or "")
    if record.get("role") == "explore":
        explore_route = _route_key_for_explore_focus(focus)
        if explore_route:
            return explore_route
    run_id = str(record.get("run_id") or "")
    text = f"{task_id} {focus} {run_id}"
    return _route_key(text)


def _route_key_for_explore_focus(focus: str) -> str:
    match = re.search(r"(?i)\bescape\s+or\s+reassess\s+`([^`]+)`", focus)
    if not match:
        match = re.search(r"(?i)\bescape\s+or\s+reassess\s+(.+?)(?:\s+after\b|[.;]|$)", focus)
    if not match:
        return ""
    title = match.group(1).strip(" `\"'")
    if not title:
        return ""
    return _route_key(title)


def _route_key(text: str) -> str:
    terms = _terms_from_text(text)
    if not terms:
        return "unassigned-route"
    return slugify("-".join(terms[:3]), 80)


def _terms_from_text(text: str) -> list[str]:
    raw = [token.lower() for token in re.findall(r"[A-Za-z][A-Za-z0-9]*|\d+[A-Za-z][A-Za-z0-9]*", text)]
    terms: list[str] = []
    for token in raw:
        if token in ROUTE_STOPWORDS:
            continue
        if re.fullmatch(r"\d{6,}", token):
            continue
        if token.startswith("2026") or token.startswith("verify"):
            continue
        if len(token) < 2:
            continue
        terms.append(token)
    return terms


def _is_blocker_fact(fact: dict[str, Any]) -> bool:
    status = str(fact.get("status") or "").lower()
    text = f"{fact.get('fact_type') or ''} {fact.get('claim_summary') or ''}".lower()
    return status == "rejected" or any(term in text for term in BLOCKER_TERMS)


def _finalize_entry(entry: dict[str, Any]) -> dict[str, Any]:
    for key in [
        "fact_ids",
        "blocker_fact_ids",
        "task_ids",
        "active_tasks",
        "blocked_tasks",
        "artifact_paths",
        "verification_ids",
        "agent_run_ids",
        "open_questions",
        "next_actions",
    ]:
        entry[key] = _unique_strs(entry.get(key) or [])
    entry["artifact_paths"] = entry["artifact_paths"][-24:]
    entry["recent_fact_summaries"] = entry.get("recent_fact_summaries", [])[-12:]
    entry["open_questions"] = entry["open_questions"][-12:]
    entry["evidence_terms"] = sorted(set(str(term) for term in entry.get("evidence_terms", []) if term))[:24]
    entry["summary"] = _entry_summary(entry)
    entry["status"] = _entry_status(entry)
    entry["health"] = _health_for_entry(entry)
    entry["updated_at"] = now_iso()
    return entry


def _entry_summary(entry: dict[str, Any]) -> str:
    return (
        f"{len(entry.get('fact_ids') or [])} fact(s), "
        f"{len(entry.get('blocker_fact_ids') or [])} blocker fact(s), "
        f"{len(entry.get('active_tasks') or [])} active task(s), "
        f"{len(entry.get('artifact_paths') or [])} artifact(s)."
    )


def _entry_status(entry: dict[str, Any]) -> str:
    active = len(entry.get("active_tasks") or [])
    blockers = len(entry.get("blocker_fact_ids") or []) + len(entry.get("blocked_tasks") or [])
    verified = sum(1 for item in entry.get("recent_fact_summaries") or [] if item.get("status") == "verified")
    if active:
        return "active"
    if blockers and not active:
        return "blocked"
    if verified:
        return "promising"
    return "stale"


def _health_for_entry(entry: dict[str, Any]) -> dict[str, Any]:
    blocker_count = len(entry.get("blocker_fact_ids") or []) + len(entry.get("blocked_tasks") or [])
    active_task_count = len(entry.get("active_tasks") or [])
    ready_count = int((entry.get("task_status_counts") or {}).get("ready", 0))
    verified_count = sum(1 for item in entry.get("recent_fact_summaries") or [] if item.get("status") == "verified")
    cooldown_ok = _explore_cooldown_ok(entry.get("last_explore_at"))
    reasons: list[str] = []
    if blocker_count >= 3 and cooldown_ok:
        reasons.append("frontier has repeated blocker evidence")
    if active_task_count >= 3 and blocker_count >= 2 and cooldown_ok:
        reasons.append("many active tasks remain inside a blocker-heavy route")
    if ready_count == 0 and blocker_count >= 3 and not active_task_count and cooldown_ok:
        reasons.append("frontier has repeated blockers and no ready or running repair task")
    explore_recommended = bool(reasons)
    focus = (
        f"Escape or reassess `{entry.get('title')}`. Use FRONTIER_INDEX fact ids "
        f"{', '.join((entry.get('fact_ids') or [])[:6])}; look for routes outside the current blocker pattern."
        if explore_recommended
        else ""
    )
    return {
        "frontier_id": entry.get("frontier_id"),
        "title": entry.get("title"),
        "status": entry.get("status"),
        "fact_count": len(entry.get("fact_ids") or []),
        "blocker_count": blocker_count,
        "verified_count": verified_count,
        "active_task_count": active_task_count,
        "ready_task_count": ready_count,
        "artifact_count": len(entry.get("artifact_paths") or []),
        "explore_run_count": int(entry.get("explore_run_count") or 0),
        "last_explore_at": entry.get("last_explore_at"),
        "cooldown_ok": cooldown_ok,
        "explore_recommended": explore_recommended,
        "reason": "; ".join(reasons),
        "recommended_focus": focus,
    }


def _global_health_report(reports: list[dict[str, Any]]) -> dict[str, Any] | None:
    blocked = [item for item in reports if int(item.get("blocker_count") or 0) > 0]
    blocker_count = sum(int(item.get("blocker_count") or 0) for item in blocked)
    active_count = sum(int(item.get("active_task_count") or 0) + int(item.get("ready_task_count") or 0) for item in reports)
    if active_count:
        return None
    if len(blocked) < GLOBAL_EXPLORE_BLOCKED_FRONTIER_THRESHOLD or blocker_count < GLOBAL_EXPLORE_BLOCKER_THRESHOLD:
        return None
    titles = [str(item.get("title") or item.get("frontier_id") or "") for item in blocked[:8]]
    focus = (
        "Run a global explore across the blocked route map. Use FRONTIER_INDEX to compare blocker patterns in "
        f"{', '.join(titles)}; propose routes outside these accumulated local minima."
    )
    return {
        "frontier_id": "global-route-map",
        "title": "Global route map",
        "status": "blocked",
        "fact_count": sum(int(item.get("fact_count") or 0) for item in reports),
        "blocker_count": blocker_count,
        "verified_count": sum(int(item.get("verified_count") or 0) for item in reports),
        "active_task_count": 0,
        "ready_task_count": 0,
        "artifact_count": sum(int(item.get("artifact_count") or 0) for item in reports),
        "explore_run_count": 0,
        "last_explore_at": None,
        "cooldown_ok": True,
        "explore_recommended": True,
        "reason": "many blocked frontiers and no ready or running work",
        "recommended_focus": focus,
    }


def _task_pool_has_active_work(project_root: Path) -> bool:
    pool = load_task_pool(project_root)
    return any(
        isinstance(task, dict) and task.get("status") in {"ready", "running", "review"}
        for task in pool.get("tasks", [])
    )


def _frontier_sort_key(entry: dict[str, Any]) -> tuple[int, int, str]:
    active = len(entry.get("active_tasks") or [])
    blockers = len(entry.get("blocker_fact_ids") or []) + len(entry.get("blocked_tasks") or [])
    return (-active, -blockers, str(entry.get("frontier_id") or ""))


def _explore_cooldown_ok(last_explore_at: object, *, minutes: int = 90) -> bool:
    if not last_explore_at:
        return True
    parsed = _parse_iso(str(last_explore_at))
    if parsed is None:
        return True
    return (datetime.now(timezone.utc) - parsed).total_seconds() >= minutes * 60


def _parse_iso(value: str) -> datetime | None:
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _max_iso(left: object, right: str) -> str:
    if not left:
        return right
    left_dt = _parse_iso(str(left))
    right_dt = _parse_iso(right)
    if left_dt is None:
        return right
    if right_dt is None:
        return str(left)
    return right if right_dt >= left_dt else str(left)


def _add_unique(items: list[Any], value: str) -> None:
    if value and value not in items:
        items.append(value)


def _unique_strs(items: list[Any]) -> list[str]:
    out: list[str] = []
    for item in items:
        value = str(item)
        if value and value not in out:
            out.append(value)
    return out


def _truncate(text: str, limit: int) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 3].rstrip() + "..."

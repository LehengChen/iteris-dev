"""Read-only lookups for iteris monitor."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from iteris.commands.context import build_context
from iteris.commands.goal import latest_goal_logs, resolve_goal_defaults, tmux_session_exists
from iteris.commands.workflow import current_run_state, default_session_name, project_sessions
from iteris.evolve import budget_status, has_evolve_state, node_root, read_state
from iteris.gitops import status as git_status
from iteris.guide.environment import check_environment
from iteris.guide.index import ROLE_FAMILY_CHILD, ROLE_FAMILY_ROOT, detect_project_role
from iteris.liveness import scan_project_liveness
from iteris.project import is_project, read_json


def _counts(items: list[Any], field: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    for item in items:
        if not isinstance(item, dict):
            continue
        value = str(item.get(field) or "(missing)")
        counts[value] = counts.get(value, 0) + 1
    return dict(sorted(counts.items()))


def _truncate_text(value: Any, limit: int = 180) -> Any:
    if not isinstance(value, str) or len(value) <= limit:
        return value
    return value[: limit - 3] + "..."


def _read_short_file(path: Path, *, limit: int = 1600) -> str:
    if not path.exists() or not path.is_file():
        return ""
    return path.read_text(encoding="utf-8", errors="replace")[:limit]


def _resolve_project_ref(value: Any, *, base: Path) -> str:
    path = Path(str(value))
    if not path.is_absolute():
        path = base / path
    return str(path.resolve())


def _compact_direction(entry: dict[str, Any]) -> dict[str, Any]:
    keys = [
        "direction_id",
        "title",
        "status",
        "tier",
        "rank",
        "kind",
        "source_node",
        "seeded_project",
        "markdown_file",
        "vetoable_until",
        "result_summary",
        "reason_summary",
    ]
    return {key: _truncate_text(entry[key]) for key in keys if key in entry}


def _compact_node(entry: dict[str, Any]) -> dict[str, Any]:
    keys = [
        "node_id",
        "project",
        "kind",
        "phase",
        "seeded_from_direction",
        "last_progress_at",
        "analyzed",
        "result_summary",
    ]
    return {key: _truncate_text(entry[key]) for key in keys if key in entry}


def _compact_boundary(entry: dict[str, Any]) -> dict[str, Any]:
    keys = ["direction_id", "verdict", "reason_summary", "recorded_at"]
    return {key: _truncate_text(entry[key]) for key in keys if key in entry}


def _compact_family_claim(entry: dict[str, Any]) -> dict[str, Any]:
    out = {
        key: _truncate_text(entry[key], 260)
        for key in ["origin_fact_id", "claim_summary", "curated_summary", "family_relevance", "substance"]
        if key in entry
    }
    sightings = entry.get("sightings")
    if isinstance(sightings, list) and sightings:
        first = next((item for item in sightings if isinstance(item, dict)), None)
        if first:
            out["project"] = _truncate_text(first.get("project"), 120)
            out["status"] = _truncate_text(first.get("status"), 80)
    return out


def _compact_failed_path(entry: dict[str, Any]) -> dict[str, Any]:
    record = entry.get("record") if isinstance(entry.get("record"), dict) else {}
    return {
        "source_project": _truncate_text(entry.get("source_project"), 120),
        "route": _truncate_text(record.get("route"), 220),
        "reason": _truncate_text(record.get("reason"), 280),
        "ts": entry.get("ts"),
    }


def _compact_fact(entry: dict[str, Any]) -> dict[str, Any]:
    keys = [
        "fact_id",
        "status",
        "fact_type",
        "review_level",
        "claim_summary",
        "path",
        "source_task",
        "verification",
        "updated_at",
    ]
    return {key: _truncate_text(entry[key], 240) for key in keys if key in entry}


def _compact_task(entry: dict[str, Any]) -> dict[str, Any]:
    keys = ["task_id", "status", "mode", "priority", "objective", "expected_outputs", "updated_at"]
    return {key: _truncate_text(entry[key], 240) for key in keys if key in entry}


def _compact_frontier(entry: dict[str, Any]) -> dict[str, Any]:
    keys = [
        "frontier_id",
        "lane_id",
        "gap_id",
        "status",
        "title",
        "summary",
        "facts",
        "tasks",
        "fact_ids",
        "task_ids",
        "blocker_fact_ids",
        "blocked_tasks",
        "active_tasks",
        "open_questions",
        "next_actions",
        "recent_fact_summaries",
        "completion_gaps",
    ]
    return {key: _truncate_text(entry[key], 240) for key in keys if key in entry}


def _compact_verification(entry: dict[str, Any]) -> dict[str, Any]:
    keys = ["request_id", "mode", "verdict", "passed", "strict_verdict", "target_artifact", "summary", "created_at"]
    return {key: _truncate_text(entry[key], 320) for key in keys if key in entry}


def _load_jsonl_dicts(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(row, dict):
            rows.append(row)
    return rows


def _recent_by_updated_at(items: list[dict[str, Any]], *, limit: int) -> list[dict[str, Any]]:
    return sorted(items, key=lambda item: str(item.get("updated_at") or item.get("ts") or ""), reverse=True)[:limit]


def _node_with_outcome(root: Path, node: dict[str, Any]) -> dict[str, Any]:
    summary = str(node.get("result_summary") or "").strip()
    if summary:
        return node
    if not node.get("project"):
        return node
    analysis = read_json(node_root(root, node) / "generalize" / "analysis.json", default=None)
    if not isinstance(analysis, dict):
        return node
    summary = str(analysis.get("result_summary") or "").strip()
    if not summary:
        return node
    enriched = dict(node)
    enriched["result_summary"] = summary
    return enriched


def _family_math_progress(root: Path) -> dict[str, Any]:
    family_index = root / "memory" / "family" / "FAMILY_INDEX.jsonl"
    failed_paths = root / "memory" / "family" / "failed_paths.jsonl"
    claims = _load_jsonl_dicts(family_index)
    failures = _load_jsonl_dicts(failed_paths)
    substance_counts: dict[str, int] = {}
    for entry in claims:
        grade = entry.get("substance")
        if isinstance(grade, str) and grade.strip():
            substance_counts[grade] = substance_counts.get(grade, 0) + 1
    return {
        "family_index_file": str(family_index),
        "failed_paths_file": str(failed_paths),
        "family_claims_total": len(claims),
        "substance_counts": dict(sorted(substance_counts.items())),
        "recent_claims": [_compact_family_claim(item) for item in _recent_by_updated_at(claims, limit=10)],
        "failed_paths_total": len(failures),
        "recent_failed_paths": [_compact_failed_path(item) for item in _recent_by_updated_at(failures, limit=6)],
        "note": "These family-ledger claims are mathematical leads; descendants must re-verify locally before relying on them.",
    }


def _project_math_progress(root: Path, context: dict[str, Any], *, target_artifact: str) -> dict[str, Any]:
    facts = _load_jsonl_dicts(root / "memory" / "facts" / "FACT_INDEX.jsonl")
    verified_facts = [item for item in facts if item.get("status") == "verified"]
    reviewed_facts = [item for item in facts if item.get("status") == "reviewed"]
    submitted_facts = [item for item in facts if item.get("status") == "submitted"]
    task_pool = context.get("task_pool") if isinstance(context.get("task_pool"), dict) else {}
    pool_tasks = [item for item in task_pool.get("tasks", []) if isinstance(item, dict)]
    ready_tasks = [item for item in context.get("ready_pool_tasks", []) if isinstance(item, dict)]
    blocked_tasks = [item for item in pool_tasks if item.get("status") == "blocked"]
    frontier = context.get("frontier_index") if isinstance(context.get("frontier_index"), dict) else {}
    completion_gaps = [item for item in (frontier.get("completion_gaps") or []) if isinstance(item, dict)]
    target_path = root / target_artifact
    lineage = read_json(root / ".iteris" / "generalize.json", default={})
    generalization: dict[str, Any] = {}
    if isinstance(lineage, dict) and lineage:
        inherited = [item for item in lineage.get("inherited_facts", []) if isinstance(item, dict)]
        generalization = {
            "lineage_file": str(root / ".iteris" / "generalize.json"),
            "evolve_root": lineage.get("evolve_root"),
            "parent_project": lineage.get("parent_project"),
            "source_result": lineage.get("source_result"),
            "direction": lineage.get("direction"),
            "inherited_facts_total": len(inherited),
            "recent_inherited_facts": inherited[-6:],
        }
    return {
        "source_file": context.get("source_file"),
        "status_file": str(root / "STATUS.md"),
        "status_excerpt": _truncate_text(context.get("status_text") or "", 1400),
        "roadmap_file": str(root / "ROADMAP.md"),
        "roadmap_excerpt": _truncate_text(context.get("roadmap_text") or "", 1400),
        "target_artifact": target_artifact,
        "target_exists": target_path.exists(),
        "target_excerpt": _read_short_file(target_path, limit=1600),
        "facts": {
            "total": len(facts),
            "by_status": _counts(facts, "status"),
            "by_type": context.get("fact_type_counts") or {},
            "recent_verified_or_reviewed": [_compact_fact(item) for item in (verified_facts + reviewed_facts)[-8:]],
            "recent_submitted": [_compact_fact(item) for item in submitted_facts[-6:]],
        },
        "frontier": {
            "summary": context.get("frontier_summary") or {},
            "health": context.get("frontier_health") or {},
            "active": [_compact_frontier(item) for item in (frontier.get("active_frontiers") or [])[:8] if isinstance(item, dict)],
            "closed_lanes": [
                _compact_frontier(item) for item in (frontier.get("closed_lanes") or [])[:6] if isinstance(item, dict)
            ],
            "completion_gaps": [_compact_frontier(item) for item in completion_gaps[:6]],
        },
        "blockers": {
            "blocked_tasks": [_compact_task(item) for item in blocked_tasks[:8]],
            "blocker_facts": [
                _compact_fact(item)
                for item in facts
                if item.get("fact_type") == "blocker" or item.get("status") == "rejected"
            ][:8],
            "completion_gaps": [_compact_frontier(item) for item in completion_gaps[:6]],
            "attention": context.get("attention") or {},
            "frontier_health": context.get("frontier_health") or {},
        },
        "tasks": {
            "total": len(pool_tasks),
            "by_status": _counts(pool_tasks, "status"),
            "active_frontier": task_pool.get("active_frontier"),
            "ready": [_compact_task(item) for item in ready_tasks[:8]],
            "recent": [_compact_task(item) for item in pool_tasks[-8:]],
        },
        "verification": {
            "recent_agent": [_compact_verification(item) for item in context.get("verification_results", [])[-5:]],
            "recent_structural": [
                _compact_verification(item) for item in context.get("structural_precheck_results", [])[-5:]
            ],
        },
        "generalization": generalization,
        "attention": context.get("attention") or {},
        "note": "This is a compact mathematical progress summary for monitor. Read the referenced files for details before making a strong mathematical judgment.",
    }


def _summarize_evolve_state(state: dict[str, Any], *, root: Path) -> dict[str, Any]:
    nodes = [_node_with_outcome(root, item) for item in state.get("nodes", []) if isinstance(item, dict)]
    pool = [item for item in state.get("direction_pool", []) if isinstance(item, dict)]
    boundary = [item for item in state.get("boundary", []) if isinstance(item, dict)]
    pending_veto = [e for e in pool if e.get("status") == "proposed" and e.get("vetoable_until")]
    actionable = [
        e
        for e in pool
        if e.get("status") in {"proposed", "approved", "seeded", "running"}
        or (e.get("status") == "blocked" and e.get("vetoable_until"))
    ]
    return {
        "state_file": str(root / "generalize" / "EVOLVE.json"),
        "goal": state.get("goal"),
        "budget": budget_status(state),
        "nodes": {
            "total": len(nodes),
            "by_phase": _counts(nodes, "phase"),
            "by_kind": _counts(nodes, "kind"),
            "recent": [_compact_node(item) for item in nodes[-12:]],
        },
        "direction_pool": {
            "total": len(pool),
            "by_status": _counts(pool, "status"),
            "by_tier": _counts(pool, "tier"),
            "pending_veto": [str(e.get("direction_id")) for e in pending_veto if e.get("direction_id")],
            "actionable": [_compact_direction(item) for item in actionable[:16]],
            "recent": [_compact_direction(item) for item in pool[-12:]],
        },
        "boundary": {
            "total": len(boundary),
            "recent": [_compact_boundary(item) for item in boundary[-8:]],
        },
        "math_progress": _family_math_progress(root),
        "note": "Monitor lookup is summarized. Read state_file for the full nodes, direction_pool, and boundary entries.",
    }


def _summarize_git_status(payload: dict[str, Any], *, limit: int = 30) -> dict[str, Any]:
    result = dict(payload)
    short = result.get("short")
    if isinstance(short, list) and len(short) > limit:
        result["short"] = short[:limit]
        result["short_omitted"] = len(short) - limit
        result["note"] = "Git short status truncated for monitor; run `git status --short` for the full list."
    return result


def lookup_doctor(project_root: Path | None) -> dict[str, Any]:
    env = check_environment()
    project = {"path": str(project_root) if project_root else ".", "is_project": False, "checks": []}
    if project_root and is_project(project_root):
        from iteris.commands.doctor import _check_project

        project = _check_project(project_root.resolve())
    return {
        "schema_version": "iteris.monitor_lookup.v0",
        "lookup": "doctor",
        "environment": env,
        "project": project,
    }


def lookup_status(project_root: Path) -> dict[str, Any]:
    root = project_root.resolve()
    session_name = default_session_name(root)
    session_live = tmux_session_exists(session_name)
    run_state = current_run_state(root)
    active = run_state.get("status") == "running" if isinstance(run_state, dict) else False
    if session_live and not active:
        active = True
    context = build_context(root, limit=5)
    _, target_artifact = resolve_goal_defaults(root)
    liveness = scan_project_liveness(root, session_name=session_name)
    return {
        "schema_version": "iteris.monitor_lookup.v0",
        "lookup": "status",
        "project_path": str(root),
        "session_name": session_name,
        "session_live": session_live,
        "run_state": run_state,
        "run_active": active,
        "target_artifact": target_artifact,
        "target_exists": (root / target_artifact).exists(),
        "sessions": project_sessions(root),
        "liveness": liveness,
        "needs_recovery": bool(liveness.get("needs_recovery")),
        "context_summary": {
            "fact_count": context.get("fact_count"),
            "facts_ok": context.get("facts_ok"),
            "ready_pool_tasks": len(context.get("ready_pool_tasks") or []),
            "attention": context.get("attention"),
        },
        "math_progress": _project_math_progress(root, context, target_artifact=target_artifact),
        "git": _summarize_git_status(git_status(root)),
        "logs": latest_goal_logs(root, session_name),
    }


def lookup_evolve_status(project_root: Path) -> dict[str, Any]:
    local_root = project_root.resolve()
    root = local_root
    role = detect_project_role(local_root)
    if role == ROLE_FAMILY_CHILD:
        entry = read_json(local_root / ".iteris" / "generalize.json", default={})
        evolve_root = entry.get("evolve_root") if isinstance(entry, dict) else None
        if isinstance(evolve_root, dict) and evolve_root.get("path"):
            root = Path(str(evolve_root["path"])).resolve()
    if not has_evolve_state(root):
        return {"schema_version": "iteris.monitor_lookup.v0", "lookup": "evolve_status", "initialized": False}
    state = read_state(root)
    summary = _summarize_evolve_state(state, root=root)
    if role == ROLE_FAMILY_CHILD:
        local_resolved = str(local_root.resolve())
        nodes = [_node_with_outcome(root, item) for item in state.get("nodes", []) if isinstance(item, dict)]
        local_nodes = [
            item
            for item in nodes
            if item.get("project") and _resolve_project_ref(item["project"], base=root) == local_resolved
        ]
        matching_nodes = [
            _compact_node(item)
            for item in local_nodes
        ]
        directions = [item for item in state.get("direction_pool", []) if isinstance(item, dict)]
        seeded_dirs = {
            item.get("seeded_from_direction")
            for item in local_nodes
            if item.get("seeded_from_direction")
        }
        summary["current_child"] = {
            "project_path": str(local_root),
            "nodes": matching_nodes,
            "directions": [
                _compact_direction(item)
                for item in directions
                if item.get("direction_id") in seeded_dirs
                or (item.get("seeded_project") and _resolve_project_ref(item["seeded_project"], base=root) == local_resolved)
            ],
        }
    return {
        "schema_version": "iteris.monitor_lookup.v0",
        "lookup": "evolve_status",
        "initialized": True,
        "family_root": str(root),
        **summary,
    }


def lookup_report_status(project_root: Path) -> dict[str, Any]:
    from iteris.reporting import report_status

    status = report_status(project_root.resolve(), include_latex=False)
    latex = status.get("latex") if isinstance(status.get("latex"), dict) else {}
    return {
        "schema_version": "iteris.monitor_lookup.v0",
        "lookup": "report_status",
        "reports_dir": status.get("reports_dir"),
        "reports_exists": status.get("reports_exists"),
        "report_index": status.get("report_index"),
        "third_party_tex_cache": status.get("third_party_tex_cache"),
        "stage_reports_dir": status.get("stage_reports_dir"),
        "fact_index": status.get("fact_index"),
        "report_count": status.get("report_count"),
        "recent_reports": status.get("recent_reports"),
        "templates": status.get("templates"),
        "styles": status.get("styles"),
        "cli": status.get("cli"),
        "switches": status.get("switches"),
        "note": (
            "Formal reports live under reports/. Evidence JSON references the fact graph "
            "with project-relative paths; portable mode hides the internal evidence appendix "
            "from rendered LaTeX without deleting evidence files."
        ),
    }


def lookup_read_status_md(project_root: Path) -> dict[str, Any]:
    path = project_root / "STATUS.md"
    if not path.exists():
        return {"lookup": "read_status_md", "exists": False, "text": ""}
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()[:120]
    return {"lookup": "read_status_md", "exists": True, "text": "\n".join(lines)}


def lookup_read_evolve_json(project_root: Path) -> dict[str, Any]:
    root = project_root.resolve()
    if not has_evolve_state(root):
        return {"lookup": "read_evolve_json", "exists": False}
    state = read_state(root)
    summary = {
        "goal": state.get("goal"),
        "budget": state.get("budget"),
        "node_count": len(state.get("nodes") or []),
        "pool_count": len(state.get("direction_pool") or []),
        "boundary_count": len(state.get("boundary") or []),
    }
    return {"lookup": "read_evolve_json", "exists": True, "summary": summary}


def default_lookups(project_root: Path | None) -> dict[str, Any]:
    payload: dict[str, Any] = {"doctor": lookup_doctor(project_root)}
    if project_root is None or not is_project(project_root):
        return payload
    root = project_root.resolve()
    payload["status"] = lookup_status(root)
    payload["read_status_md"] = lookup_read_status_md(root)
    role = detect_project_role(root)
    if role in {ROLE_FAMILY_ROOT, ROLE_FAMILY_CHILD}:
        payload["evolve_status"] = lookup_evolve_status(root)
    if role == ROLE_FAMILY_ROOT:
        payload["read_evolve_json"] = lookup_read_evolve_json(root)
    if (root / "reports").is_dir() and any((root / "reports").glob("*/report.json")):
        payload["report_status"] = lookup_report_status(root)
    return payload


_KEYWORD_LOOKUPS = {
    "evolve": "evolve_status",
    "budget": "evolve_status",
    "direction": "evolve_status",
    "veto": "evolve_status",
    "进度": "status",
    "status": "status",
    "跑": "status",
    "run": "status",
    "recover": "status",
    "phase": "read_status_md",
    "report": "report_status",
    "latex": "report_status",
    ".tex": "report_status",
    "paper": "report_status",
    "报告": "report_status",
    "论文": "report_status",
}


def lookups_for_message(project_root: Path | None, message: str, *, base: dict[str, Any] | None = None) -> dict[str, Any]:
    result = dict(base or default_lookups(project_root))
    if project_root is None or not is_project(project_root):
        return result
    root = project_root.resolve()
    lower = message.lower()
    for key, lookup_name in _KEYWORD_LOOKUPS.items():
        if key in lower or key in message:
            if lookup_name == "evolve_status":
                result["evolve_status"] = lookup_evolve_status(root)
            elif lookup_name == "status":
                result["status"] = lookup_status(root)
            elif lookup_name == "read_status_md":
                result["read_status_md"] = lookup_read_status_md(root)
            elif lookup_name == "report_status":
                result["report_status"] = lookup_report_status(root)
    return result


def format_lookups(lookups: dict[str, Any]) -> str:
    return json.dumps(lookups, indent=2, ensure_ascii=False)

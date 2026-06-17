"""Boundary inheritance from a prior Iteris project on the same problem.

A fresh restart on a problem that already has an explored project loses the
most expensive knowledge the old run produced: verified blockers, refuted
claims, and closed lanes. `inherit_boundary` imports exactly that boundary as
*advisory* knowledge — facts enter the child as `reviewed` (never `verified`),
wrapped with provenance, and the parent's closed lanes / do-not-schedule
patterns are merged into the child's FRONTIER_INDEX so the new run can route
around known dead ends without trusting them blindly.

Exposed as `iteris new --inherit-frontier <parent>` and
`iteris tool frontier inherit . --from <parent>`.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from iteris.frontier import _is_blocker_fact, load_frontier_index, save_frontier_index
from iteris.memory.facts import parse_frontmatter, rebuild_fact_index, write_fact
from iteris.memory.scratch import append_event
from iteris.memory.search import load_jsonl
from iteris.project import (
    now_iso,
    project_id_from_path,
    read_json,
    require_project,
    write_json,
)

INHERITED_SOURCE_TASK = "task-boundary-inheritance"
INHERIT_LINEAGE_REL_PATH = ".iteris/inherit.json"
MAX_IMPORTED_FACTS = 200


def select_boundary_rows(parent_root: Path) -> list[dict[str, Any]]:
    """Boundary facts of a project: rejected claims and verified blocker facts.

    Ordered for truncation under the import cap: rejected claims first, then
    verified blockers; within each group newest parent fact-file mtime first
    (fact files carry no creation timestamp; mtime is the best recency proxy
    and late facts reflect the parent's most mature boundary).
    """
    rows = load_jsonl(parent_root / "memory" / "facts" / "FACT_INDEX.jsonl")
    selected: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict) or not row.get("fact_id"):
            continue
        status = str(row.get("status") or "")
        if str(row.get("fact_type") or "") == "source_problem":
            continue
        if status == "rejected":
            selected.append({**row, "boundary_kind": "rejected_claim"})
        elif status == "verified" and _is_blocker_fact(row):
            selected.append({**row, "boundary_kind": "verified_blocker"})

    def sort_key(row: dict[str, Any]) -> tuple[int, float]:
        path = parent_root / str(row.get("path") or "")
        mtime = path.stat().st_mtime if path.exists() else 0.0
        return (0 if row["boundary_kind"] == "rejected_claim" else 1, -mtime)

    selected.sort(key=sort_key)
    return selected


def inherit_boundary(child_root: Path, parent_root: Path, *, max_facts: int = MAX_IMPORTED_FACTS) -> dict[str, Any]:
    """Import the parent project's boundary into the child as advisory knowledge.

    Re-imports are idempotent per fact (deterministic child ids overwrite in
    place). Re-running with a smaller ``max_facts`` keeps previously imported
    extras in memory/facts/ — the summary and lineage records describe the
    latest import only; nothing is deleted.
    """
    child_root = require_project(child_root)
    parent_root = require_project(parent_root)
    if child_root == parent_root:
        raise ValueError("cannot inherit a project's boundary into itself")
    if max_facts < 1:
        raise ValueError("max_facts must be at least 1")
    child_id = project_id_from_path(child_root)
    parent_id = project_id_from_path(parent_root)

    rows = select_boundary_rows(parent_root)
    truncated = max(0, len(rows) - max_facts)
    rows = rows[:max_facts]

    imported: list[dict[str, Any]] = []
    for row in rows:
        imported.append(_import_boundary_fact(child_root, parent_root, child_id, parent_id, row))

    patterns = _merge_frontier_boundary(child_root, parent_root, parent_id, imported)
    summary_rel = _write_summary(child_root, parent_root, parent_id, imported, truncated)
    rebuild_fact_index(child_root)

    lineage = {
        "schema_version": "iteris.inherit.v0",
        "parent_project": parent_id,
        "parent_path": str(parent_root),
        "created_at": now_iso(),
        "imported_facts": [
            {
                "fact_id": item["fact_id"],
                "parent_fact_id": item["parent_fact_id"],
                "boundary_kind": item["boundary_kind"],
            }
            for item in imported
        ],
        "do_not_schedule_patterns": patterns,
        "summary_path": summary_rel,
        "truncated_fact_count": truncated,
    }
    _merge_lineage(child_root, lineage)
    append_event(
        child_root,
        "boundary_inherited",
        {
            "parent_project": parent_id,
            "imported_fact_count": len(imported),
            "do_not_schedule_patterns": len(patterns),
            "summary_path": summary_rel,
            "truncated_fact_count": truncated,
        },
    )
    return {
        "parent_project": parent_id,
        "parent_path": str(parent_root),
        "imported_facts": [item["fact_id"] for item in imported],
        "boundary_kinds": _kind_counts(imported),
        "do_not_schedule_patterns": patterns,
        "summary_path": summary_rel,
        "lineage_path": INHERIT_LINEAGE_REL_PATH,
        "truncated_fact_count": truncated,
    }


def _import_boundary_fact(
    child_root: Path,
    parent_root: Path,
    child_id: str,
    parent_id: str,
    row: dict[str, Any],
) -> dict[str, Any]:
    parent_fact_id = str(row["fact_id"])
    kind = str(row["boundary_kind"])
    local = parent_fact_id.split(":", 2)[-1] if parent_fact_id.count(":") >= 2 else parent_fact_id.removeprefix("fact:")
    # The parent id is part of the child fact id so that inheriting from two
    # prior attempts with identical local fact names cannot silently collide.
    child_fact_id = f"fact:{child_id}:inherited-boundary:{parent_id}:{local}"
    origin = str(row.get("origin_fact_id") or parent_fact_id)
    statement_body = _parent_statement(parent_root, row)
    summary = str(row.get("claim_summary") or "(no claim summary recorded)")
    if kind == "rejected_claim":
        wrapped_summary = f"Inherited boundary: claim was attempted and REJECTED in {parent_id}: {summary}"
        framing = (
            f"The claim below was attempted in the prior project `{parent_id}` and its verification "
            "was rejected. Treat it as boundary knowledge: do not re-attempt this route without a "
            "genuinely new idea, and do not treat the claim itself as true."
        )
    else:
        wrapped_summary = f"Inherited boundary: verified blocker in {parent_id}: {summary}"
        framing = (
            f"The blocker below was verified in the prior project `{parent_id}`. It is advisory here: "
            "use it to route around known dead ends, and re-verify locally before making it load-bearing "
            "in any proof or assembly."
        )
    statement = (
        f"{framing}\n\n"
        f"Provenance: parent fact `{parent_fact_id}` (status `{row.get('status')}`"
        + (f", verification `{row.get('verification')}`" if row.get("verification") else "")
        + ").\n\n"
        "### original statement\n\n"
        f"{statement_body}"
    )
    write_fact(
        child_root,
        fact_id=child_fact_id,
        source_task=INHERITED_SOURCE_TASK,
        claim_summary=wrapped_summary,
        statement=statement,
        status="reviewed",
        fact_type="inherited_boundary",
        notes=f"Imported by `inherit_boundary` from {parent_root}.",
        claim_policy="inherited_boundary_advisory",
        review_level="none",
        origin_fact_id=origin,
    )
    return {
        "fact_id": child_fact_id,
        "parent_fact_id": parent_fact_id,
        "boundary_kind": kind,
        "claim_summary": wrapped_summary,
    }


def _parent_statement(parent_root: Path, row: dict[str, Any]) -> str:
    rel = str(row.get("path") or "")
    path = parent_root / rel
    if not rel or not path.exists():
        return "(parent fact file unavailable; see claim summary)"
    try:
        _, body = parse_frontmatter(path.read_text(encoding="utf-8", errors="replace"))
    except ValueError:
        return "(parent fact file unparseable; see claim summary)"
    return _statement_section(body)


def _statement_section(body: str) -> str:
    marker = "## statement"
    lower = body.lower()
    start = lower.find(marker)
    if start < 0:
        return body.strip() or "(empty statement)"
    rest = body[start + len(marker) :]
    end = rest.find("\n## ")
    section = rest if end < 0 else rest[:end]
    return section.strip() or "(empty statement)"


def _merge_frontier_boundary(
    child_root: Path,
    parent_root: Path,
    parent_id: str,
    imported: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    parent_frontier = load_frontier_index(parent_root)
    child_frontier = load_frontier_index(child_root)

    inherited_patterns: list[dict[str, Any]] = []
    for source_field in ["do_not_schedule_patterns", "closed_lanes"]:
        for item in parent_frontier.get(source_field) or []:
            if not isinstance(item, dict):
                item = {"pattern": str(item)}
            inherited_patterns.append(
                {
                    **item,
                    "inherited_from": parent_id,
                    "inherited_source_field": source_field,
                }
            )

    existing = [
        item
        for item in child_frontier.get("do_not_schedule_patterns") or []
        if not (isinstance(item, dict) and item.get("inherited_from") == parent_id)
    ]
    child_frontier["do_not_schedule_patterns"] = existing + inherited_patterns
    save_frontier_index(child_root, child_frontier)
    return inherited_patterns


def _write_summary(
    child_root: Path,
    parent_root: Path,
    parent_id: str,
    imported: list[dict[str, Any]],
    truncated: int,
) -> str:
    rel = f"references/processed/inherited-boundary-{parent_id}.md"
    path = child_root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    kinds = _kind_counts(imported)
    lines = [
        f"# Inherited boundary from `{parent_id}`",
        "",
        f"Imported {len(imported)} advisory boundary fact(s) from `{parent_root}` "
        f"({kinds.get('verified_blocker', 0)} verified blocker(s), {kinds.get('rejected_claim', 0)} rejected claim(s)).",
        "",
        "These facts are `reviewed`, NOT `verified`: use them to avoid re-exploring known dead",
        "lanes, re-verify any of them before making them load-bearing, and treat a rejected",
        "claim's statement as a failed route, not as truth.",
        "",
    ]
    if truncated:
        lines.extend([f"NOTE: {truncated} additional boundary fact(s) were not imported (cap reached); see the parent project directly.", ""])
    for item in imported:
        lines.append(f"- `{item['fact_id']}` [{item['boundary_kind']}] — {item['claim_summary']}")
    lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")
    return rel


def _merge_lineage(child_root: Path, lineage: dict[str, Any]) -> None:
    path = child_root / INHERIT_LINEAGE_REL_PATH
    existing = read_json(path, default=None)
    parents: list[dict[str, Any]] = []
    if isinstance(existing, dict):
        parents = [
            item
            for item in existing.get("parents") or []
            if isinstance(item, dict) and item.get("parent_project") != lineage["parent_project"]
        ]
    parents.append(lineage)
    write_json(path, {"schema_version": "iteris.inherit.v0", "updated_at": now_iso(), "parents": parents})


def _kind_counts(imported: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for item in imported:
        kind = str(item.get("boundary_kind"))
        counts[kind] = counts.get(kind, 0) + 1
    return counts

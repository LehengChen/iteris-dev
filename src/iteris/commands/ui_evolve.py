"""Dashboard data contract for the evolve supervisor (`iteris tool ui evolve|supervision`).

Kept out of commands/ui.py so each data-contract module stays small; ui.py
mounts these commands onto the same `iteris tool ui` typer app via register().
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import typer

from iteris.project import require_project


def register(app: typer.Typer) -> None:
    app.command()(evolve)
    app.command()(supervision)
    app.command()(direction)
    app.command()(node)
    app.command()(family)
    app.command()(reports)


# --------------------------------------------------------------------------- #
# Outcome resolution
#
# A direction's intent markdown is written at proposal time and never updated,
# so the *outcome* of a finished node lives elsewhere: result_summary in the
# child's generalize/analysis.json (backfilled onto the EVOLVE.json node entry
# once analysis is ingested), the final answer behind STATUS.md's
# target_artifact, and the curated claims in memory/family/FAMILY_INDEX.jsonl.
# --------------------------------------------------------------------------- #


def _node_result_summary(root: Path, node_entry: dict[str, Any]) -> str | None:
    """Post-run summary of what the node actually proved, if available.

    Prefers the copy on the EVOLVE.json node entry; falls back to reading the
    child project's analysis.json directly so nodes finished before the
    backfill (or not yet ingested) still surface an outcome.
    """
    from iteris.evolve import node_root

    summary = str(node_entry.get("result_summary") or "").strip()
    if summary:
        return summary
    analysis_path = node_root(root, node_entry) / "generalize" / "analysis.json"
    try:
        analysis = json.loads(analysis_path.read_text(encoding="utf-8", errors="replace"))
    except (OSError, ValueError):
        return None
    summary = str(analysis.get("result_summary") or "").strip() if isinstance(analysis, dict) else ""
    return summary or None


def _node_answer(root: Path, node_entry: dict[str, Any]) -> dict[str, Any] | None:
    """The node's final answer document, located via STATUS.md target_artifact."""
    from iteris.evolve import node_root

    project = node_root(root, node_entry)
    try:
        status_text = (project / "STATUS.md").read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    rel = next(
        (line.split(":", 1)[1].strip() for line in status_text.splitlines() if line.startswith("target_artifact:")),
        None,
    )
    if not rel:
        return None
    answer_path = (project / rel).resolve()
    if not answer_path.is_relative_to(project.resolve()):
        return None
    try:
        return {"path": rel, "content": answer_path.read_text(encoding="utf-8", errors="replace")}
    except OSError:
        return {"path": rel, "content": None}


def _origin_node_of(row: dict[str, Any]) -> str | None:
    """FAMILY_INDEX entry → originating node id (`fact:<project-id>:…`)."""
    fact_id = str(row.get("origin_fact_id") or "")
    parts = fact_id.split(":")
    if len(parts) >= 3 and parts[0] == "fact":
        return parts[1]
    sightings = row.get("sightings") or []
    return sightings[0].get("project") if sightings else None


def _family_claims(root: Path, *, node_id: str | None = None) -> list[dict[str, Any]]:
    """Curated family-ledger claims, newest first; optionally one node's only."""
    from iteris.memory.family import load_family_index

    claims: list[dict[str, Any]] = []
    for row in load_family_index(root):
        origin = _origin_node_of(row)
        sightings = row.get("sightings") or []
        if node_id is not None and origin != node_id and not any(s.get("project") == node_id for s in sightings):
            continue
        claims.append(
            {
                "origin_fact_id": row.get("origin_fact_id"),
                "origin_node": origin,
                "claim_summary": row.get("claim_summary"),
                "curated_summary": row.get("curated_summary"),
                "family_relevance": row.get("family_relevance"),
                "updated_at": row.get("updated_at"),
                "sightings": [{"project": s.get("project"), "status": s.get("status")} for s in sightings],
            }
        )
    claims.sort(key=lambda c: str(c.get("updated_at") or ""), reverse=True)
    return claims


def evolve(
    project_path: str = typer.Argument(".", help="Family root project."),
    json_output: bool = typer.Option(False, "--json", help="Print machine-readable JSON."),
) -> None:
    """Evolve state for the dashboard: nodes, direction pool, budget, boundary.

    Unlike `iteris evolve status`, a project without EVOLVE.json is not an
    error — the dashboard polls every project and hides the view when
    `initialized` is false.
    """
    from iteris.commands.evolve import _tmux_session_exists
    from iteris.commands.workflow import evolve_session_name
    from iteris.evolve import budget_status, has_evolve_state, read_state

    root = require_project(project_path)
    if not has_evolve_state(root):
        typer.echo(json.dumps({"schema_version": "iteris.ui_evolve.v0", "initialized": False}))
        return
    state = read_state(root)
    pool = state.get("direction_pool", [])
    session_name = evolve_session_name(root)
    payload: dict[str, Any] = {
        "schema_version": "iteris.ui_evolve.v0",
        "initialized": True,
        "goal": state.get("goal"),
        "session_name": session_name,
        "session_live": _tmux_session_exists(session_name),
        "budget": budget_status(state),
        "nodes": state.get("nodes", []),
        "direction_pool": pool,
        "pending_veto": [e["direction_id"] for e in pool if e.get("status") == "proposed" and e.get("vetoable_until")],
        "boundary": state.get("boundary", []),
        "updated_at": state.get("updated_at"),
    }
    typer.echo(json.dumps(payload, indent=2 if json_output else None, ensure_ascii=False))


def direction(
    project_path: str = typer.Argument(".", help="Family root project."),
    direction_id: str = typer.Option(..., "--direction-id", help="Direction id from the pool."),
    json_output: bool = typer.Option(False, "--json", help="Print machine-readable JSON."),
) -> None:
    """One direction's full story: intent markdown, seeded node, offspring, boundary.

    Synthesis directions point their markdown_file into sibling child projects
    (e.g. ../<child>/generalize/auto-*.md) — that is by design, so the path is
    resolved relative to the family root without a containment check.
    """
    from iteris.evolve import has_evolve_state, read_state

    root = require_project(project_path)
    payload: dict[str, Any] = {"schema_version": "iteris.ui_direction.v0", "direction": None}
    if has_evolve_state(root):
        state = read_state(root)
        pool = state.get("direction_pool", [])
        entry = next((e for e in pool if e.get("direction_id") == direction_id), None)
        if entry is not None:
            payload["direction"] = entry
            md = entry.get("markdown_file")
            if md:
                md_path = (root / md).resolve()
                try:
                    payload["content"] = md_path.read_text(encoding="utf-8", errors="replace")
                except OSError as exc:
                    payload["content_error"] = str(exc)
            seeded = next(
                (n for n in state.get("nodes", []) if n.get("seeded_from_direction") == direction_id), None
            )
            if seeded is not None:
                seeded = {**seeded, "result_summary": _node_result_summary(root, seeded)}
            payload["seeded_node"] = seeded
            if seeded is not None:
                payload["children_directions"] = [
                    {k: e.get(k) for k in ("direction_id", "title", "status", "kind", "proposed_at")}
                    for e in pool
                    if e.get("source_node") == seeded.get("node_id")
                ]
            payload["boundary"] = next(
                (b for b in state.get("boundary", []) if b.get("direction_id") == direction_id), None
            )
    typer.echo(json.dumps(payload, indent=2 if json_output else None, ensure_ascii=False))


def node(
    project_path: str = typer.Argument(".", help="Family root project."),
    node_id: str = typer.Option(..., "--node-id", help="Node id from EVOLVE.json."),
    json_output: bool = typer.Option(False, "--json", help="Print machine-readable JSON."),
) -> None:
    """One child node's outcome story: result summary, final answer, curated claims.

    The answer document lives in the child project (resolved via its STATUS.md
    target_artifact) and ships inline — it is the self-contained record of what
    the node proved.
    """
    from iteris.evolve import has_evolve_state, read_state

    root = require_project(project_path)
    payload: dict[str, Any] = {"schema_version": "iteris.ui_evolve_node.v0", "node": None}
    if has_evolve_state(root):
        state = read_state(root)
        entry = next((n for n in state.get("nodes", []) if n.get("node_id") == node_id), None)
        if entry is not None:
            payload["node"] = entry
            payload["result_summary"] = _node_result_summary(root, entry)
            payload["answer"] = _node_answer(root, entry)
            payload["family_claims"] = _family_claims(root, node_id=node_id)
    typer.echo(json.dumps(payload, indent=2 if json_output else None, ensure_ascii=False))


def family(
    project_path: str = typer.Argument(".", help="Family root project."),
    json_output: bool = typer.Option(False, "--json", help="Print machine-readable JSON."),
) -> None:
    """The family's accumulated intelligence: curated claims and known dead ends.

    Claims come from memory/family/FAMILY_INDEX.jsonl (curated by the evolve
    supervisor after each node's facts are analyzed), dead ends from
    failed_paths.jsonl; both newest first.
    """
    from iteris.memory.family import failed_paths_path

    root = require_project(project_path)
    failed: list[dict[str, Any]] = []
    fp = failed_paths_path(root)
    if fp.exists():
        for line in fp.read_text(encoding="utf-8", errors="replace").splitlines():
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            record = row.get("record") or {}
            failed.append(
                {
                    "ts": row.get("ts"),
                    "source_project": row.get("source_project"),
                    "route": record.get("route"),
                    "reason": record.get("reason"),
                }
            )
    payload = {
        "schema_version": "iteris.ui_family.v0",
        "claims": _family_claims(root),
        "failed_paths": failed[::-1],
    }
    typer.echo(json.dumps(payload, indent=2 if json_output else None, ensure_ascii=False))


def reports(
    project_path: str = typer.Argument(".", help="Family root project."),
    json_output: bool = typer.Option(False, "--json", help="Print machine-readable JSON."),
    limit: int = typer.Option(20, "--limit", help="Maximum reports to return (newest first)."),
) -> None:
    """Immutable supervisor stage reports (artifacts/reports/*/report.md), newest first.

    Reports are small narrative markdown written once per milestone, so the
    full content ships inline — no per-report detail endpoint needed.
    """
    root = require_project(project_path)
    reports_dir = root / "artifacts" / "reports"
    items: list[dict[str, Any]] = []
    if reports_dir.is_dir():
        for entry in sorted((p for p in reports_dir.iterdir() if p.is_dir()), reverse=True)[:limit]:
            report_md = entry / "report.md"
            if not report_md.is_file():
                continue
            content = report_md.read_text(encoding="utf-8", errors="replace")
            first_line = content.lstrip().split("\n", 1)[0]
            items.append(
                {
                    "report_id": entry.name,
                    "headline": first_line.lstrip("# ").strip(),
                    "created_at": _stamp_to_iso(entry.name),
                    "path": str(report_md.relative_to(root)),
                    "content": content,
                }
            )
    payload = {"schema_version": "iteris.ui_reports.v0", "items": items}
    typer.echo(json.dumps(payload, indent=2 if json_output else None, ensure_ascii=False))


def _stamp_to_iso(name: str) -> str | None:
    """`20260610T204325796722Z-node-verified` → `2026-06-10T20:43:25Z`."""
    stamp = name.split("-", 1)[0]
    if len(stamp) >= 16 and stamp[8] == "T":
        d, t = stamp[:8], stamp[9:15]
        return f"{d[:4]}-{d[4:6]}-{d[6:8]}T{t[:2]}:{t[2:4]}:{t[4:6]}Z"
    return None


def supervision(
    project_path: str = typer.Argument(".", help="Family root project."),
    json_output: bool = typer.Option(False, "--json", help="Print machine-readable JSON."),
    limit: int = typer.Option(120, "--limit", help="Maximum journal entries to return (newest first)."),
) -> None:
    """Supervision journal tail for the dashboard, newest first.

    Supersession is computed over the full journal (an intent's outcome may
    fall outside the returned tail), so each returned entry carries a final
    `superseded` flag.
    """
    from iteris.supervision.journal import read_entries

    root = require_project(project_path)
    entries = read_entries(root)
    superseded = {row.get("supersedes") for row in entries if row.get("supersedes")}
    tail = entries[-limit:] if limit else entries
    items = [{**row, "superseded": row.get("entry_id") in superseded} for row in reversed(tail)]
    payload = {"schema_version": "iteris.ui_supervision.v0", "items": items}
    typer.echo(json.dumps(payload, indent=2 if json_output else None, ensure_ascii=False))

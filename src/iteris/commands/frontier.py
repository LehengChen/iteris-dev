"""Frontier map commands."""

from __future__ import annotations

import json

import typer

from iteris import log
from iteris.frontier import frontier_health, load_frontier_index, refresh_frontier_from_project, set_active_frontier, validate_frontier_index
from iteris.inherit import MAX_IMPORTED_FACTS, inherit_boundary
from iteris.project import require_project

app = typer.Typer(help="Inspect and maintain the project frontier map.")


@app.command("inherit")
def inherit(
    project_path: str = typer.Argument(".", help="Iteris project path (the child that receives the boundary)."),
    from_project: str = typer.Option(..., "--from", help="Prior Iteris project on the same problem."),
    max_facts: int = typer.Option(MAX_IMPORTED_FACTS, "--max-facts", min=1, help="Import cap. Rejected claims are kept first, then newest boundary facts. Re-running with a smaller cap keeps previously imported extras."),
    json_output: bool = typer.Option(False, "--json", help="Print machine-readable JSON."),
) -> None:
    """Import a prior project's verified blockers and rejected claims as advisory boundary facts."""
    root = require_project(project_path)
    parent = require_project(from_project)
    result = inherit_boundary(root, parent, max_facts=max_facts)
    if json_output:
        typer.echo(json.dumps(result, indent=2, ensure_ascii=False))
        return
    kinds = result.get("boundary_kinds") or {}
    log.key_value(
        {
            "Parent": str(result["parent_project"]),
            "Imported facts": str(len(result["imported_facts"])),
            "Verified blockers": str(kinds.get("verified_blocker", 0)),
            "Rejected claims": str(kinds.get("rejected_claim", 0)),
            "Do-not-schedule patterns": str(len(result["do_not_schedule_patterns"])),
            "Summary": str(result["summary_path"]),
        }
    )
    if result.get("truncated_fact_count"):
        log.warn(f"{result['truncated_fact_count']} boundary fact(s) beyond the import cap were skipped")
    log.success("Boundary inherited as advisory (reviewed) knowledge")


@app.command("show")
def show(
    project_path: str = typer.Argument(".", help="Iteris project path."),
    json_output: bool = typer.Option(False, "--json", help="Print machine-readable JSON."),
) -> None:
    """Show memory/facts/FRONTIER_INDEX.json."""
    root = require_project(project_path)
    payload = load_frontier_index(root)
    if json_output:
        typer.echo(json.dumps(payload, indent=2, ensure_ascii=False))
        return
    log.key_value(
        {
            "Active frontiers": str(len(payload.get("active_frontiers") or [])),
            "Closed lanes": str(len(payload.get("closed_lanes") or [])),
            "Completion gaps": str(len(payload.get("completion_gaps") or [])),
            "Updated": str(payload.get("updated_at")),
        }
    )


@app.command("validate")
def validate(
    project_path: str = typer.Argument(".", help="Iteris project path."),
    json_output: bool = typer.Option(False, "--json", help="Print machine-readable JSON."),
) -> None:
    """Validate the frontier map shape."""
    root = require_project(project_path)
    result = validate_frontier_index(root)
    if json_output:
        typer.echo(json.dumps(result, indent=2, ensure_ascii=False))
        raise typer.Exit(0 if result["ok"] else 1)
    if result["ok"]:
        log.success("Frontier map is valid")
    else:
        log.warn(f"Frontier map has {len(result['errors'])} error(s)")
    rows = [(item["location"], item["issue"], "") for item in [*result["errors"], *result["warnings"]]]
    log.results_table(rows or [("ok", "no frontier issues", "")], title="Frontier validation")
    raise typer.Exit(0 if result["ok"] else 1)


@app.command("refresh")
def refresh(
    project_path: str = typer.Argument(".", help="Iteris project path."),
    json_output: bool = typer.Option(False, "--json", help="Print machine-readable JSON."),
) -> None:
    """Refresh a compact active frontier from TASK_POOL and FACT_INDEX."""
    root = require_project(project_path)
    payload = refresh_frontier_from_project(root)
    if json_output:
        typer.echo(json.dumps(payload, indent=2, ensure_ascii=False))
        return
    log.success("Refreshed task-pool frontier")


@app.command("health")
def health(
    project_path: str = typer.Argument(".", help="Iteris project path."),
    json_output: bool = typer.Option(False, "--json", help="Print machine-readable JSON."),
) -> None:
    """Report whether the current frontier map suggests a new explore subagent."""
    root = require_project(project_path)
    result = frontier_health(root)
    if json_output:
        typer.echo(json.dumps(result, indent=2, ensure_ascii=False))
        return
    status = "recommended" if result.get("explore_recommended") else "not recommended"
    log.key_value(
        {
            "Explore": status,
            "Reason": str(result.get("reason") or ""),
            "Needs refresh": str(result.get("needs_refresh", False)).lower(),
        }
    )
    rows = [
        (
            str(item.get("frontier_id")),
            str(item.get("status")),
            str(item.get("blocker_count")),
            str(item.get("active_task_count")),
            str(item.get("reason") or ""),
        )
        for item in result.get("frontiers", [])
    ]
    log.results_table(rows or [("none", "skipped", "0", "0", "run frontier refresh first")], title="Frontier health")


@app.command("set-active")
def set_active(
    project_path: str = typer.Argument(".", help="Iteris project path."),
    frontier_id: str = typer.Option(..., "--frontier-id", help="Stable frontier id."),
    title: str = typer.Option(..., "--title", help="Short frontier title."),
    summary: str = typer.Option(..., "--summary", help="One-sentence frontier summary."),
    task: list[str] | None = typer.Option(None, "--task", help="Referenced task id. Repeatable."),
    fact: list[str] | None = typer.Option(None, "--fact", help="Referenced fact id. Repeatable."),
    gap: list[str] | None = typer.Option(None, "--gap", help="Known completion gap. Repeatable."),
    json_output: bool = typer.Option(False, "--json", help="Print machine-readable JSON."),
) -> None:
    """Upsert one active frontier entry."""
    root = require_project(project_path)
    payload = set_active_frontier(root, frontier_id=frontier_id, title=title, summary=summary, tasks=task, facts=fact, gaps=gap)
    if json_output:
        typer.echo(json.dumps(payload, indent=2, ensure_ascii=False))
        return
    log.success(f"Updated active frontier {frontier_id}")

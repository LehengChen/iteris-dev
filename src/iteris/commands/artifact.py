"""Artifact index, search, and maintainability gates."""

from __future__ import annotations

import json

import typer

from iteris import log
from iteris.artifacts import artifact_gate, artifact_index_summary, search_artifacts
from iteris.project import require_project


app = typer.Typer(help="Inspect artifact indexes and run artifact maintainability gates.")


@app.command("index")
def index(
    project_path: str = typer.Argument(".", help="Iteris project path."),
    limit: int = typer.Option(20, "--limit", "-n", help="Recent records to include."),
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    """Show the global artifact index summary."""
    root = require_project(project_path)
    result = artifact_index_summary(root, limit=limit)
    if json_output:
        typer.echo(json.dumps(result, indent=2, ensure_ascii=False))
        return
    log.key_value(
        {
            "Index": result["artifact_index"],
            "Records": str(result["index_record_count"]),
            "Manifests": str(result["manifest_count"]),
        }
    )
    rows = []
    for record in result["recent_records"]:
        rows.append(
            (
                str(record.get("record_type") or "?"),
                str(record.get("mode") or "-"),
                str(record.get("task_id") or record.get("run_id") or "-")[:90],
            )
        )
    log.results_table(rows or [("none", "skipped", "no artifact records")], title="Artifact index")


@app.command("gate")
def gate(
    project_path: str = typer.Argument(".", help="Iteris project path."),
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    """Check artifact index/manifest coverage and script maintainability."""
    root = require_project(project_path)
    result = artifact_gate(root)
    if json_output:
        typer.echo(json.dumps(result, indent=2, ensure_ascii=False))
        raise typer.Exit(0 if result["ok"] else 1)
    if result["ok"]:
        log.success(f"Artifact gate passed: {result['manifest_count']} manifests, {result['index_record_count']} index records")
    else:
        log.warn(f"Artifact gate failed: {len(result['errors'])} errors")
    rows = [(item["location"], item["issue"], "") for item in [*result["errors"], *result["warnings"]]]
    log.results_table(rows or [("ok", "no artifact gate issues", "")], title="Artifact gate")
    raise typer.Exit(0 if result["ok"] else 1)


@app.command("search")
def search(
    project_path: str = typer.Argument(".", help="Iteris project path."),
    query: str = typer.Argument(..., help="Search query."),
    limit: int = typer.Option(10, "--limit", "-n"),
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    """Search artifact index records and manifests."""
    root = require_project(project_path)
    result = search_artifacts(root, query=query, limit=limit)
    if json_output:
        typer.echo(json.dumps(result, indent=2, ensure_ascii=False))
        return
    rows = []
    for item in result:
        label = item.get("path") or (item.get("record") or {}).get("artifact_manifest") or (item.get("record") or {}).get("run_id") or "record"
        summary = item.get("summary") or item.get("task_id") or item.get("kind")
        rows.append((str(label), str(item.get("score", 0)), str(summary)[:180]))
    log.results_table(rows or [("none", "0", "no matching artifacts")], title="Artifact search")

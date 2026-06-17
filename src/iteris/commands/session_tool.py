"""Session housekeeping commands."""

from __future__ import annotations

import json
from pathlib import Path

import typer

from iteris import log
from iteris.sessions import gc_sessions, reapable_sessions

app = typer.Typer(help="Tmux session housekeeping for Iteris projects.")


@app.command("gc")
def gc(
    workspace: str = typer.Argument(
        ".", help="Directory whose Iteris projects' finished sessions should be reaped."
    ),
    dry_run: bool = typer.Option(False, "--dry-run", help="Report candidates without killing."),
    json_output: bool = typer.Option(False, "--json", help="Print machine-readable JSON."),
) -> None:
    """Kill worker sessions of terminal-phase projects and analyze sessions
    whose analysis.json exists. Evolve masters and unknown sessions are never
    touched."""
    root = Path(workspace).resolve()
    if not root.is_dir():
        raise typer.BadParameter(f"not a directory: {root}")
    results = gc_sessions(root, dry_run=dry_run)
    if json_output:
        typer.echo(json.dumps({"count": len(results), "sessions": results}, indent=2, ensure_ascii=False))
        return
    if not results:
        log.info("no reapable sessions")
        return
    rows = [
        (
            item["session_name"],
            ("dry-run" if dry_run else ("killed" if item.get("killed") else "FAILED")),
            f"{item['kind']}: {item['reason']}",
        )
        for item in results
    ]
    log.results_table(rows, title="Session GC")


@app.command("list")
def list_cmd(
    workspace: str = typer.Argument(".", help="Directory to scan."),
    json_output: bool = typer.Option(False, "--json", help="Print machine-readable JSON."),
) -> None:
    """List reap candidates without killing anything."""
    root = Path(workspace).resolve()
    results = reapable_sessions(root)
    if json_output:
        typer.echo(json.dumps({"count": len(results), "sessions": results}, indent=2, ensure_ascii=False))
        return
    rows = [(i["session_name"], i["kind"], i["reason"]) for i in results]
    log.results_table(rows or [("none", "-", "nothing reapable")], title="Reapable sessions")

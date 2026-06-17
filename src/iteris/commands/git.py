"""Git workflow commands for Iteris projects."""

from __future__ import annotations

import json

import typer

from iteris import log
from iteris.gitops import GitError, checkpoint as make_checkpoint, init_git, status as git_status
from iteris.project import require_project

app = typer.Typer(help="Manage project git state and checkpoints.")


@app.command("init")
def init(
    project_path: str = typer.Argument(".", help="Iteris project path."),
    initial_branch: str = typer.Option("main", "--initial-branch"),
    json_output: bool = typer.Option(False, "--json", help="Print machine-readable JSON."),
) -> None:
    """Initialize git for an Iteris project and write a project .gitignore."""
    root = require_project(project_path)
    try:
        result = init_git(root, initial_branch=initial_branch)
    except GitError as exc:
        raise typer.BadParameter(str(exc)) from exc
    if json_output:
        typer.echo(json.dumps(result, indent=2, ensure_ascii=False))
        return
    log.key_value(
        {
            "Repo": "created" if result["created"] else "already present",
            "Gitignore": "updated" if result["gitignore_changed"] else "unchanged",
            "Branch": result["status"].get("branch") or "(none)",
        }
    )


@app.command("status")
def status(
    project_path: str = typer.Argument(".", help="Iteris project path."),
    json_output: bool = typer.Option(False, "--json", help="Print machine-readable JSON."),
) -> None:
    """Show concise git state for an Iteris project."""
    root = require_project(project_path)
    result = git_status(root)
    if json_output:
        typer.echo(json.dumps(result, indent=2, ensure_ascii=False))
        return
    if not result["repo"]:
        log.warn("Not a git repository. Run `iteris tool git init`.")
        return
    rows = [(line[:3].strip() or "?", "changed", line[3:].strip()) for line in result["short"]]
    log.key_value({"Repo": "yes", "Branch": result["branch"], "Dirty": str(result["dirty"]).lower()})
    log.results_table(rows or [("ok", "ok", "working tree clean")], title="Git status")


@app.command("checkpoint")
def checkpoint(
    project_path: str = typer.Argument(".", help="Iteris project path."),
    message: str = typer.Option(..., "--message", "-m", help="Commit message."),
    path: list[str] = typer.Option([], "--path", "-p", help="Path to include. Repeatable. Defaults to all files."),
    allow_agent_identity: bool = typer.Option(
        True,
        "--allow-agent-identity/--no-agent-identity",
        help="Set project-local fallback git identity when user identity is missing.",
    ),
    json_output: bool = typer.Option(False, "--json", help="Print machine-readable JSON."),
) -> None:
    """Stage project changes and create a git checkpoint commit."""
    root = require_project(project_path)
    try:
        result = make_checkpoint(root, message=message, paths=path or None, allow_agent_identity=allow_agent_identity)
    except GitError as exc:
        raise typer.BadParameter(str(exc)) from exc
    if json_output:
        typer.echo(json.dumps(result, indent=2, ensure_ascii=False))
        return
    if not result["committed"]:
        log.info(result["reason"])
        return
    log.success(f"Created checkpoint {result['commit']}")

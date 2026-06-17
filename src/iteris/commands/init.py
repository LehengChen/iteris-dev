"""Project initialization command."""

from __future__ import annotations

from pathlib import Path

import typer

from iteris import log
from iteris.project import init_project


def init(
    project_path: str = typer.Argument(".", help="Project directory to create or update. Defaults to the current directory."),
    source: str | None = typer.Option(None, "--source", "-s", help="Optional source problem file to copy into sources/."),
    force: bool = typer.Option(False, "--force", help="Overwrite Iteris-managed project files."),
) -> None:
    """Initialize an Iteris project."""
    log.header("iteris tool init")
    result = init_project(Path(project_path), source=Path(source) if source else None, force=force)
    log.key_value({"Project": result["project_path"], "Project id": result["project_id"], "Source": result["source"] or "(none)"})
    log.success("Project initialized")

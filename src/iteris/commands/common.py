"""Shared CLI helpers."""

from __future__ import annotations

from pathlib import Path

import typer

from iteris.project import require_project


def require_public_project(project_path: str | Path) -> Path:
    try:
        return require_project(project_path)
    except FileNotFoundError as exc:
        root = Path(project_path).resolve()
        raise typer.BadParameter(f"not an Iteris project: {root}. Run `iteris new --source /path/to/problem.tex` first.") from exc

"""Version command."""

from __future__ import annotations

from pathlib import Path

import typer

from iteris import __version__, log
from iteris.deploy import deployed_commit, skew_warning
from iteris.project import is_project


def version(project_path: str = typer.Argument(".", help="Optional Iteris project path.")) -> None:
    root = Path(project_path).resolve()
    info = {"CLI version": __version__}
    # Surface the deployed git commit so stale code is never silent.
    info["Deployed commit"] = deployed_commit() or "unknown (unstamped install — use scripts/deploy.sh)"
    if is_project(root):
        info["Project"] = str(root)
        info["Project state"] = "initialized"
    else:
        info["Project"] = f"not an Iteris project: {root}"
    log.key_value(info)
    warning = skew_warning()
    if warning:
        log.warn(f"deploy skew: {warning}")


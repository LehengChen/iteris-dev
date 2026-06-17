"""Tmux session housekeeping: classify and reap sessions whose work is done.

Iteris session kinds per project (see ``commands/workflow.py``):
``iteris-<slug>`` (worker run), ``iteris-analyze-<slug>`` (direction
analysis), ``iteris-evolve-<slug>`` (evolve master). A worker session is
reapable when its project's STATUS phase is terminal; an analyze session is
reapable when ``generalize/analysis.json`` exists (the analysis deliverable
is complete). Evolve masters and sessions that match no known project are
NEVER touched.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

from iteris.project import is_project, session_slug, slugify

TERMINAL_PHASES = {"goal_success_verified", "verified", "complete"}


def list_tmux_sessions() -> list[str]:
    try:
        proc = subprocess.run(
            ["tmux", "list-sessions", "-F", "#{session_name}"],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return []
    if proc.returncode != 0:
        return []
    return [line.strip() for line in proc.stdout.splitlines() if line.strip()]


def kill_tmux_session(session_name: str) -> bool:
    try:
        proc = subprocess.run(
            ["tmux", "kill-session", "-t", session_name],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False
    return proc.returncode == 0


def project_phase(project_root: Path) -> str | None:
    status = project_root / "STATUS.md"
    if not status.exists():
        return None
    for line in status.read_text(encoding="utf-8", errors="replace").splitlines():
        if line.startswith("phase:"):
            return line.split(":", 1)[1].strip()
    return None


def reapable_sessions(workspace: Path) -> list[dict[str, Any]]:
    """Sessions in ``workspace`` whose project has finished the matching work.

    Scans the workspace's Iteris projects, computes their session names, and
    returns reap candidates (live sessions only). Conservative by
    construction: unknown sessions, evolve masters, and projects in
    non-terminal phases are never candidates.
    """
    live = set(list_tmux_sessions())
    candidates: list[dict[str, Any]] = []
    for project in sorted(workspace.iterdir()):
        if not project.is_dir() or not is_project(project):
            continue
        slug = session_slug(project.name)
        phase = project_phase(project)

        worker = f"iteris-{slug}"
        if worker in live and phase in TERMINAL_PHASES:
            candidates.append(
                {
                    "session_name": worker,
                    "kind": "worker",
                    "project": str(project),
                    "reason": f"phase {phase}",
                }
            )
        analyze = f"iteris-analyze-{slug}"
        if analyze in live and (project / "generalize" / "analysis.json").exists():
            candidates.append(
                {
                    "session_name": analyze,
                    "kind": "analyze",
                    "project": str(project),
                    "reason": "analysis.json produced",
                }
            )
    return candidates


def gc_sessions(workspace: Path, *, dry_run: bool = False) -> list[dict[str, Any]]:
    """Kill reapable sessions (or just report them with ``dry_run``)."""
    results = []
    for candidate in reapable_sessions(workspace):
        entry = dict(candidate)
        if dry_run:
            entry["killed"] = False
            entry["dry_run"] = True
        else:
            entry["killed"] = kill_tmux_session(candidate["session_name"])
        results.append(entry)
    return results

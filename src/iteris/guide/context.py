"""Assemble monitor handoff prompts from guide docs and lookups."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from iteris.guide.index import framework_guide_index_text, framework_operator_text, read_project_index
from iteris.guide.lookups import format_lookups
from iteris.guide.paths import project_operator_docs_path, project_operator_runtime_path
from iteris.guide.prompt import monitor_handoff_footer, monitor_session_mode, monitor_system
from iteris.project import is_project


def _truncate(text: str, limit: int = 8000) -> str:
    if len(text) <= limit:
        return text
    return text[: limit - 40] + "\n\n...(truncated)...\n"


def _operator_excerpt(root: Path | None) -> str:
    if root is None or not is_project(root):
        return _truncate(framework_operator_text())
    for path in (project_operator_runtime_path(root), project_operator_docs_path(root)):
        if path.exists():
            return _truncate(path.read_text(encoding="utf-8", errors="replace"))
    return _truncate(framework_operator_text())


def _context_hints(role: str) -> list[str]:
    if role == "family_root":
        return [
            "lookups.evolve_status.math_progress",
            "lookups.evolve_status.direction_pool",
            "lookups.evolve_status.nodes",
            "lookups.evolve_status.boundary",
        ]
    if role == "family_child":
        return [
            "lookups.status.math_progress",
            "lookups.status.math_progress.generalization",
            "lookups.evolve_status.current_child",
            "lookups.evolve_status.math_progress",
        ]
    if role == "single":
        return [
            "lookups.status.math_progress",
            "lookups.status.math_progress.frontier",
            "lookups.status.math_progress.facts",
            "lookups.status.math_progress.tasks",
        ]
    return ["lookups.doctor"]


def _task_critical_snapshot(lookups: dict[str, Any]) -> dict[str, Any]:
    status = lookups.get("status") if isinstance(lookups.get("status"), dict) else {}
    progress = status.get("math_progress") if isinstance(status.get("math_progress"), dict) else {}
    evolve = lookups.get("evolve_status") if isinstance(lookups.get("evolve_status"), dict) else {}
    report_status = lookups.get("report_status") if isinstance(lookups.get("report_status"), dict) else {}
    snapshot: dict[str, Any] = {}
    if status:
        snapshot["status"] = {
            "project_path": status.get("project_path"),
            "run_state": status.get("run_state"),
            "run_active": status.get("run_active"),
            "needs_recovery": status.get("needs_recovery"),
            "target_artifact": status.get("target_artifact"),
            "target_exists": status.get("target_exists"),
            "session_live": status.get("session_live"),
        }
    if progress:
        snapshot["project_progress"] = {
            "status_excerpt": progress.get("status_excerpt"),
            "target_artifact": progress.get("target_artifact"),
            "target_exists": progress.get("target_exists"),
            "facts": progress.get("facts"),
            "frontier": progress.get("frontier"),
            "blockers": progress.get("blockers"),
            "tasks": progress.get("tasks"),
            "generalization": progress.get("generalization"),
        }
    if evolve:
        snapshot["evolve"] = {
            "goal": evolve.get("goal"),
            "budget": evolve.get("budget"),
            "nodes": evolve.get("nodes"),
            "direction_pool": evolve.get("direction_pool"),
            "boundary": evolve.get("boundary"),
            "math_progress": evolve.get("math_progress"),
            "current_child": evolve.get("current_child"),
        }
    if report_status:
        snapshot["report"] = {
            "reports_dir": report_status.get("reports_dir"),
            "report_count": report_status.get("report_count"),
            "recent_reports": report_status.get("recent_reports"),
            "templates": report_status.get("templates"),
            "styles": report_status.get("styles"),
        }
    return snapshot


def build_monitor_handoff(
    *,
    project_root: Path | None,
    user_message: str,
    lookups: dict[str, Any],
    role: str,
    executor: str,
    locale: str = "zh",
) -> str:
    sections = [
        monitor_system(locale),
        "\n# SESSION MODE\n",
        monitor_session_mode(locale),
        "\n",
        "\n# MONITOR HANDOFF\n",
        json.dumps(
            {
                "schema_version": "iteris.monitor_handoff.v0",
                "project_path": str(project_root) if project_root else None,
                "project_role": role,
                "executor": executor,
                "user_message": user_message,
                "context_hints": [
                    *_context_hints(role),
                    *(["lookups.report_status"] if "report_status" in lookups else []),
                ],
            },
            indent=2,
            ensure_ascii=False,
        ),
        "\n",
        "# TASK-CRITICAL SNAPSHOT\n",
        json.dumps(_task_critical_snapshot(lookups), indent=2, ensure_ascii=False),
        "\n",
        "# GUIDE_INDEX\n",
        framework_guide_index_text(),
    ]
    if project_root is not None and is_project(project_root):
        index_path = project_root / ".iteris" / "INDEX.md"
        if index_path.exists():
            sections.extend(["\n# PROJECT INDEX\n", index_path.read_text(encoding="utf-8", errors="replace")])
        else:
            sections.extend(["\n# PROJECT INDEX\n", json.dumps(read_project_index(project_root), indent=2)])
        sections.extend(["\n# PROJECT OPERATOR (excerpt)\n", _operator_excerpt(project_root)])
    else:
        sections.extend(["\n# FRAMEWORK OPERATOR (excerpt)\n", _operator_excerpt(None)])

    sections.extend(["\n# LIVE LOOKUPS (read-only JSON)\n", format_lookups(lookups)])
    sections.extend(["\n# USER MESSAGE\n", user_message, "\n\n", monitor_handoff_footer(locale), "\n"])
    return "".join(sections)

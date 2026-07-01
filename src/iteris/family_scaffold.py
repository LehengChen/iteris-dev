"""Scaffolding helpers for `iteris family new` and `iteris family init`."""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

from iteris.commands.new import perform_new_project
from iteris.family import (
    FAMILY_STATE_SCHEMA,
    family_state_path,
    write_family_marker,
    write_family_state,
)
from iteris.project import is_project, now_iso, read_json, session_slug, write_json


def parse_sibling_spec(spec: str) -> dict[str, Any]:
    """Parse CLI sibling spec: id=2.6,path=child,session=...,target=...,gaps=A|B."""
    parts: dict[str, Any] = {}
    for item in spec.split(","):
        item = item.strip()
        if not item or "=" not in item:
            continue
        key, value = item.split("=", 1)
        key = key.strip().lower()
        value = value.strip()
        if key == "id":
            parts["sibling_id"] = value
        elif key == "path":
            parts["path"] = value
        elif key == "session":
            parts["session"] = value
        elif key in {"target", "target_artifact"}:
            parts["target_artifact"] = value
        elif key == "gaps":
            parts["gaps"] = [gap.strip() for gap in value.split("|") if gap.strip()]
        elif key == "claim_prefix":
            parts["claim_prefix"] = value
        elif key == "north_star":
            parts["north_star"] = value
        elif key == "priority":
            try:
                parts["priority"] = int(value)
            except ValueError:
                parts["priority"] = 0
        elif key == "baseline_cite_only":
            parts["baseline_cite_only"] = value.lower() in {"1", "true", "yes", "on"}
    if not parts.get("sibling_id") or not parts.get("path"):
        raise ValueError(f"invalid sibling spec (need id= and path=): {spec}")
    return parts


def _default_family_id(family_root: Path) -> str:
    return family_root.name


def _ensure_family_dirs(family_root: Path) -> None:
    (family_root / ".iteris").mkdir(parents=True, exist_ok=True)
    (family_root / "memory" / "family").mkdir(parents=True, exist_ok=True)
    (family_root / "docs").mkdir(parents=True, exist_ok=True)
    (family_root / "references").mkdir(parents=True, exist_ok=True)


def _write_watchdog_goal(project_root: Path, north_star: str | None) -> None:
    if not north_star:
        return
    path = project_root / ".iteris" / "watchdog_goal.txt"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(north_star.strip() + "\n", encoding="utf-8")


def _write_family_operator_stub(family_root: Path, goal: str, siblings: list[dict[str, Any]]) -> None:
    path = family_root / "docs" / "FAMILY_OPERATOR.md"
    if path.exists():
        return
    lines = [
        "# Family operator notes",
        "",
        f"North-Star goal: {goal}",
        "",
        "## Siblings",
        "",
    ]
    for sibling in siblings:
        sid = sibling.get("sibling_id")
        gaps = sibling.get("gaps") or []
        session = sibling.get("session") or "(default)"
        lines.append(f"- **{sid}** — path `{sibling.get('path')}` — session `{session}` — gaps: {', '.join(gaps) or '(none)'}")
    lines.extend(["", "Maintain gap tables, session names, and alignment rules here.", ""])
    path.write_text("\n".join(lines), encoding="utf-8")


def _register_sibling_marker(
    family_root: Path,
    sibling: dict[str, Any],
    *,
    family_id: str,
) -> None:
    project = (family_root / str(sibling["path"])).resolve()
    write_family_marker(
        project,
        {
            "family_root": str(family_root.resolve()),
            "family_id": family_id,
            "sibling_id": sibling.get("sibling_id"),
        },
    )


def perform_family_init(
    family_root: Path,
    *,
    goal: str,
    siblings: list[dict[str, Any]],
    max_concurrent: int = 2,
    stall_hours: float = 18.0,
    family_id: str | None = None,
    adopt_symlinks: bool = False,
    claim_prefix: str | None = None,
) -> dict[str, Any]:
    family_root = family_root.resolve()
    _ensure_family_dirs(family_root)

    normalized: list[dict[str, Any]] = []
    for sibling in siblings:
        entry = dict(sibling)
        rel_path = Path(str(entry["path"]))
        project = (family_root / rel_path).resolve()
        if adopt_symlinks and not project.exists():
            raise ValueError(f"sibling path missing: {project}")
        if not is_project(project):
            raise ValueError(f"not an Iteris project: {project}")
        if not entry.get("session"):
            entry["session"] = f"iteris-{session_slug(project.name)}"
        normalized.append(entry)
        _register_sibling_marker(family_root, entry, family_id=family_id or _default_family_id(family_root))

    state = {
        "schema_version": FAMILY_STATE_SCHEMA,
        "goal": goal,
        "created_at": now_iso(),
        "schedule": {"max_concurrent": max_concurrent, "stall_hours": stall_hours},
        "policy": {
            "prefer_watchdog_goal": True,
            "allow_principled_stop": False,
            "claim_prefix": claim_prefix or "",
        },
        "run": {
            "started_at": None,
            "watchdog_session": f"iteris-{session_slug(family_root.name)}-watchdog",
            "last_tick_at": None,
        },
        "siblings": normalized,
    }
    write_family_state(family_root, state)
    _write_family_operator_stub(family_root, goal, normalized)
    return {"family_root": str(family_root), "siblings": len(normalized), "state_file": str(family_state_path(family_root))}


def perform_family_new(
    family_root: Path,
    *,
    manifest_path: Path | None = None,
    goal: str | None = None,
    siblings: list[dict[str, Any]] | None = None,
    max_concurrent: int = 2,
    stall_hours: float = 18.0,
    shared_references: Path | None = None,
) -> dict[str, Any]:
    family_root = family_root.resolve()
    _ensure_family_dirs(family_root)

    manifest: dict[str, Any] = {}
    if manifest_path is not None:
        payload = read_json(manifest_path, default={})
        if isinstance(payload, dict):
            manifest = payload

    resolved_goal = goal or manifest.get("goal")
    if not resolved_goal:
        raise ValueError("family goal is required (--goal or manifest.goal)")

    manifest_siblings = manifest.get("siblings") or []
    if siblings:
        resolved_siblings = siblings
    elif isinstance(manifest_siblings, list):
        resolved_siblings = [dict(item) for item in manifest_siblings if isinstance(item, dict)]
    else:
        resolved_siblings = []

    if not resolved_siblings:
        raise ValueError("at least one sibling is required")

    family_id = str(manifest.get("family_id") or _default_family_id(family_root))
    schedule = manifest.get("schedule") if isinstance(manifest.get("schedule"), dict) else {}
    policy = manifest.get("policy") if isinstance(manifest.get("policy"), dict) else {}

    try:
        resolved_max = int(schedule.get("max_concurrent", max_concurrent))
    except (TypeError, ValueError):
        resolved_max = max_concurrent
    try:
        resolved_stall = float(schedule.get("stall_hours", stall_hours))
    except (TypeError, ValueError):
        resolved_stall = stall_hours

    refs_src = shared_references
    if refs_src is None and manifest.get("shared_references"):
        refs_src = Path(str(manifest["shared_references"]))

    created: list[dict[str, Any]] = []
    for sibling in resolved_siblings:
        rel = str(sibling["path"])
        project = family_root / rel
        source = sibling.get("source")
        if not source:
            raise ValueError(f"sibling {sibling.get('sibling_id')} missing source for family new")
        source_path = Path(str(source))
        if not source_path.is_absolute():
            source_path = (manifest_path.parent / source_path).resolve() if manifest_path else source_path.resolve()
        if not source_path.exists():
            raise ValueError(f"source file not found for sibling {sibling.get('sibling_id')}: {source_path}")

        perform_new_project(project, source=source_path, allow_non_empty=True)
        _write_watchdog_goal(project, sibling.get("north_star"))
        if refs_src and refs_src.exists():
            dest = family_root / "references"
            dest.mkdir(parents=True, exist_ok=True)
            for item in refs_src.iterdir():
                target = dest / item.name
                if item.is_dir():
                    if not target.exists():
                        shutil.copytree(item, target)
                elif item.is_file() and not target.exists():
                    shutil.copy2(item, target)

        if not sibling.get("session"):
            sibling["session"] = f"iteris-{session_slug(project.name)}"
        _register_sibling_marker(family_root, sibling, family_id=family_id)
        created.append({"sibling_id": sibling.get("sibling_id"), "path": rel, "project": str(project.resolve())})

    state = {
        "schema_version": FAMILY_STATE_SCHEMA,
        "goal": resolved_goal,
        "created_at": now_iso(),
        "schedule": {"max_concurrent": resolved_max, "stall_hours": resolved_stall},
        "policy": {
            "prefer_watchdog_goal": policy.get("prefer_watchdog_goal", True),
            "allow_principled_stop": policy.get("allow_principled_stop", False),
            "claim_prefix": policy.get("claim_prefix") or manifest.get("claim_prefix") or "",
        },
        "run": {
            "started_at": None,
            "watchdog_session": f"iteris-{session_slug(family_root.name)}-watchdog",
            "last_tick_at": None,
        },
        "siblings": resolved_siblings,
    }
    write_family_state(family_root, state)
    _write_family_operator_stub(family_root, resolved_goal, resolved_siblings)
    return {
        "family_root": str(family_root),
        "family_id": family_id,
        "siblings_created": created,
        "state_file": str(family_state_path(family_root)),
    }

"""Project INDEX.md generation and refresh."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from iteris.commands.goal.targets import resolve_goal_defaults
from iteris.commands.workflow import default_session_name, evolve_session_name
from iteris.evolve import evolve_path, has_evolve_state, read_state
from iteris.generalize import read_evolve_root
from iteris.guide.paths import (
    package_data_path,
    project_index_path,
    project_operator_docs_path,
    project_operator_runtime_path,
    read_package_text,
)
from iteris.project import is_project, now_iso, project_id_from_path, read_json, session_slug, source_file

INDEX_SCHEMA = "iteris.project_index.v0"
ROLE_NONE = "none"
ROLE_SINGLE = "single"
ROLE_FAMILY_ROOT = "family_root"
ROLE_FAMILY_CHILD = "family_child"


def detect_project_role(root: Path) -> str:
    root = root.resolve()
    if not is_project(root):
        return ROLE_NONE
    if has_evolve_state(root):
        return ROLE_FAMILY_ROOT
    evolve_root = read_evolve_root(root)
    if evolve_root:
        return ROLE_FAMILY_CHILD
    return ROLE_SINGLE


def _title_from_toml(root: Path) -> str:
    text = (root / "iteris.toml").read_text(encoding="utf-8", errors="replace")
    match = re.search(r'^title\s*=\s*"([^"]*)"', text, re.MULTILINE)
    return match.group(1) if match else root.name


def build_project_index(root: Path) -> dict[str, Any]:
    root = root.resolve()
    role = detect_project_role(root)
    project_id = project_id_from_path(root)
    src = source_file(root)
    source_rel = str(src.relative_to(root)) if src else None
    _, target_artifact = resolve_goal_defaults(root)

    payload: dict[str, Any] = {
        "schema_version": INDEX_SCHEMA,
        "updated_at": now_iso(),
        "project_id": project_id,
        "project_path": str(root),
        "title": _title_from_toml(root),
        "role": role,
        "family_root": None,
        "node_id": project_id,
        "source_file": source_rel,
        "target_artifact": target_artifact,
        "evolve": {
            "initialized": False,
            "state_file": "generalize/EVOLVE.json",
            "goal": None,
            "supervisor_session": evolve_session_name(root),
        },
        "pointers": {
            "status": "STATUS.md",
            "operator": "docs/OPERATOR.md",
            "task_pool": "tasks/TASK_POOL.json",
            "facts_index": "memory/facts/FACT_INDEX.jsonl",
            "reports": "reports",
            "report_index": "reports/REPORT_INDEX.jsonl",
            "rolling_report": ".iteris/supervision/REPORT.md",
            "family_memory": "memory/family/FAMILY_INDEX.jsonl",
        },
        "commands": {
            "start_worker": "iteris run",
            "start_evolve": "iteris evolve run",
            "check_status": "iteris status --json",
            "check_family": "iteris evolve status --json",
            "observe_ui": "iteris dashboard",
            "recover": "iteris recover",
            "monitor": "iteris monitor",
            "report_status": "iteris report status",
            "report_new": "iteris report new --template amsart --style theory",
        },
    }

    if role == ROLE_FAMILY_ROOT:
        payload["evolve"]["initialized"] = True
        if evolve_path(root).exists():
            state = read_json(evolve_path(root), default={})
            if isinstance(state, dict):
                payload["evolve"]["goal"] = state.get("goal")
    elif role == ROLE_FAMILY_CHILD:
        entry = read_evolve_root(root) or {}
        payload["family_root"] = entry.get("path")
        payload["node_id"] = entry.get("node_id") or project_id

    payload["commands"]["worker_session"] = default_session_name(root)
    return payload


def render_project_index(payload: dict[str, Any], *, notes: str = "") -> str:
    body = notes.strip()
    front = json.dumps(payload, indent=2, ensure_ascii=False)
    if body:
        return f"---json\n{front}\n---\n\n{body}\n"
    return f"---json\n{front}\n---\n"


def parse_project_index(text: str) -> dict[str, Any]:
    if not text.strip():
        return {}
    if text.startswith("---json"):
        parts = text.split("---", 2)
        if len(parts) >= 2:
            raw = parts[1]
            if raw.startswith("json"):
                raw = raw[4:].strip()
            try:
                data = json.loads(raw)
                return data if isinstance(data, dict) else {}
            except json.JSONDecodeError:
                return {}
    try:
        data = json.loads(text)
        return data if isinstance(data, dict) else {}
    except json.JSONDecodeError:
        return {}


def read_project_index(root: Path) -> dict[str, Any]:
    path = project_index_path(root)
    if not path.exists():
        return {}
    return parse_project_index(path.read_text(encoding="utf-8"))


def _existing_notes(root: Path) -> str:
    current = read_project_index(root)
    path = project_index_path(root)
    if not path.exists():
        return ""
    text = path.read_text(encoding="utf-8")
    if text.startswith("---json") or text.startswith("---"):
        parts = text.split("---", 2)
        if len(parts) >= 3:
            return parts[2].strip()
    return ""


def write_project_index(root: Path, *, notes: str | None = None) -> Path:
    root = root.resolve()
    payload = build_project_index(root)
    note_text = _existing_notes(root) if notes is None else notes
    path = project_index_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_project_index(payload, notes=note_text), encoding="utf-8")
    return path


def refresh_project_index(root: Path) -> Path | None:
    if not is_project(root):
        return None
    return write_project_index(root)


def index_needs_refresh(root: Path) -> bool:
    index_path = project_index_path(root)
    toml_path = root / "iteris.toml"
    if not index_path.exists():
        return True
    if not toml_path.exists():
        return False
    return toml_path.stat().st_mtime > index_path.stat().st_mtime


def seed_project_operator(root: Path) -> Path:
    docs_path = project_operator_docs_path(root)
    docs_path.parent.mkdir(parents=True, exist_ok=True)
    if docs_path.exists():
        return docs_path
    template_path = package_data_path("templates/project_OPERATOR.md.tpl")
    template = template_path.read_text(encoding="utf-8")
    src = source_file(root)
    _, target = resolve_goal_defaults(root)
    text = template.format(
        title=_title_from_toml(root),
        source_file=str(src.relative_to(root)) if src else "(none)",
        target_artifact=target,
    )
    docs_path.write_text(text, encoding="utf-8")
    return docs_path


def sync_operator_copy(root: Path) -> Path | None:
    docs_path = project_operator_docs_path(root)
    runtime_path = project_operator_runtime_path(root)
    if not docs_path.exists():
        seed_project_operator(root)
    if docs_path.exists():
        runtime_path.parent.mkdir(parents=True, exist_ok=True)
        runtime_path.write_text(docs_path.read_text(encoding="utf-8"), encoding="utf-8")
        return runtime_path
    return None


def ensure_project_guide_files(root: Path) -> None:
    """Create INDEX, docs/OPERATOR, .iteris/monitor, and synced operator copy."""
    root = root.resolve()
    if not is_project(root):
        return
    (root / ".iteris" / "monitor").mkdir(parents=True, exist_ok=True)
    seed_project_operator(root)
    write_project_index(root)
    sync_operator_copy(root)


def framework_guide_index_text() -> str:
    return read_package_text("GUIDE_INDEX.md")


def framework_operator_text() -> str:
    return read_package_text("OPERATOR.md")

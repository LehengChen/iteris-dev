"""Project layout and initialization helpers."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


PROJECT_DIRS = [
    "sources",
    "references",
    "references/user",
    "references/processed",
    "artifacts/runs",
    "artifacts/agent_runs",
    "artifacts/route_checks",
    "artifacts/references",
    "artifacts/code",
    "artifacts/experiments",
    "artifacts/proofs",
    "artifacts/run_bundles",
    "results",
    "memory/facts",
    "memory/scratch",
    "memory/summaries",
    "tasks",
    "verification/requests",
    "verification/results",
    "verification/agent_runs",
    ".iteris/prompts",
    ".iteris/skills",
    ".iteris/logs",
]


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def now_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")


def slugify(text: str, limit: int = 80) -> str:
    out = re.sub(r"[^A-Za-z0-9._-]+", "-", text.strip().lower())
    out = re.sub(r"-+", "-", out).strip("-._")
    return (out or "item")[:limit]


def session_slug(name: str, limit: int = 30) -> str:
    """Collision-proof slug for tmux session names.

    Plain truncation gave two projects sharing a ``limit``-char prefix the
    SAME session name — in a real evolve run the supervisor then matched a
    verified sibling against the new worker's live session and reaped it.
    Names short enough to survive untruncated keep their historical form;
    longer names trade the last 7 chars for a stable digest of the full name.
    """
    full = slugify(name, 10_000)
    if len(full) <= limit:
        return full
    digest = hashlib.sha1(full.encode("utf-8")).hexdigest()[:6]
    return f"{full[: limit - 7].rstrip('-._')}-{digest}"


def resolve_project(path: str | Path) -> Path:
    return Path(path).resolve()


def is_project(path: str | Path) -> bool:
    root = resolve_project(path)
    return (root / "iteris.toml").exists()


def require_project(path: str | Path) -> Path:
    root = resolve_project(path)
    if not is_project(root):
        raise FileNotFoundError(f"not an Iteris project: {root}")
    return root


def read_json(path: Path, default: Any = None) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def write_json(path: Path, payload: Any) -> None:
    # Atomic write: a concurrent reader (e.g. `verify wait` polling a result
    # file as the verifier writes it) must never observe a truncated file. A
    # plain write_text leaves the path empty between truncate and flush; a
    # temp-file + os.replace makes the swap atomic on the same filesystem.
    path.parent.mkdir(parents=True, exist_ok=True)
    data = json.dumps(payload, indent=2, ensure_ascii=False) + "\n"
    fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=path.name + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(data)
        os.replace(tmp_name, path)
    except BaseException:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def append_jsonl(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n")


def project_id_from_path(path: Path) -> str:
    return slugify(path.name, limit=60)


def init_project(root: Path, *, source: Path | None = None, force: bool = False) -> dict[str, Any]:
    root = root.resolve()
    root.mkdir(parents=True, exist_ok=True)
    for rel in PROJECT_DIRS:
        (root / rel).mkdir(parents=True, exist_ok=True)

    project_id = project_id_from_path(root)
    created_at = now_iso()

    copied_source = None
    if source is not None:
        source = source.resolve()
        if not source.exists():
            raise FileNotFoundError(f"source file not found: {source}")
        dest = root / "sources" / source.name
        if not dest.exists() or force:
            shutil.copy2(source, dest)
        copied_source = dest

    config_path = root / "iteris.toml"
    if not config_path.exists() or force:
        source_line = f'source_file = "sources/{copied_source.name}"\n' if copied_source else 'source_file = ""\n'
        config_path.write_text(
            "[project]\n"
            f'id = "{project_id}"\n'
            f'title = "{root.name}"\n'
            f"created_at = \"{created_at}\"\n"
            + source_line,
            encoding="utf-8",
        )

    if not (root / ".iteris" / "config.json").exists() or force:
        write_json(
            root / ".iteris" / "config.json",
            {
                "schema_version": "iteris.config.v0",
                "goal_mode": {
                    "codex_command": "codex --yolo",
                    "tmux_session_prefix": "iteris",
                },
                "verification": {
                    "backend": "agent",
                    "default_mode": "source",
                    "service_port": 8092,
                },
            },
        )

    _write_if_missing(root / "PROJECT.md", f"# {root.name}\n\nProject initialized by Iteris.\n")
    _write_if_missing(root / "references" / "README.md", "# References\n\nPut user-provided papers, notes, PDFs, or source material here.\n")
    _write_if_missing(
        root / "artifacts" / "README.md",
        "# Artifacts\n\n"
        "Use `ARTIFACT_INDEX.jsonl` as the global append-only artifact index. "
        "Subagent workspaces live under mode-specific folders such as `proofs/`, "
        "`experiments/`, `code/`, and `route_checks/`; each workspace has an "
        "`artifact_manifest.json`.\n",
    )
    _write_if_missing(root / "ROADMAP.md", "# Roadmap\n\n- Initialize project memory.\n- Explore source problem.\n- Submit first verification request.\n")
    _write_if_missing(root / "STATUS.md", "phase: initialized\nlast_updated: null\n")
    _write_if_missing(root / "artifacts" / "ARTIFACT_INDEX.jsonl", "")
    _write_if_missing(root / "tasks" / "TASK_BOARD.jsonl", "")
    if not (root / "tasks" / "TASK_POOL.json").exists() or force:
        write_json(
            root / "tasks" / "TASK_POOL.json",
            {
                "schema_version": "iteris.task_pool.v0",
                "project_id": project_id,
                "updated_at": created_at,
                "active_frontier": "",
                "tasks": [],
            },
        )
    _write_if_missing(root / "verification" / "VERIFICATION_INDEX.jsonl", "")
    _write_if_missing(root / ".iteris" / "logs" / "events.jsonl", "")
    _write_if_missing(root / "memory" / "scratch" / "events.jsonl", "")
    _write_if_missing(root / "memory" / "scratch" / "observations.jsonl", "")
    _write_if_missing(root / "memory" / "scratch" / "failed_paths.jsonl", "")
    _write_if_missing(root / "memory" / "scratch" / "branch_states.jsonl", "")
    _write_if_missing(root / "memory" / "scratch" / "decisions.jsonl", "")
    _write_if_missing(root / "memory" / "facts" / "FACT_INDEX.jsonl", "")
    if not (root / "memory" / "facts" / "FRONTIER_INDEX.json").exists() or force:
        write_json(
            root / "memory" / "facts" / "FRONTIER_INDEX.json",
            {
                "schema_version": "iteris.frontier_index.v0",
                "project_id": project_id,
                "updated_at": created_at,
                "active_frontiers": [],
                "reviewed_positive_routes": [],
                "closed_lanes": [],
                "submitted_gates": [],
                "completion_gaps": [],
                "do_not_schedule_patterns": [],
            },
        )

    (root / ".iteris" / "monitor").mkdir(parents=True, exist_ok=True)
    try:
        from iteris.guide.index import ensure_project_guide_files

        ensure_project_guide_files(root)
    except Exception:
        pass

    return {
        "project_id": project_id,
        "project_path": str(root),
        "source": str(copied_source) if copied_source else None,
    }


def _write_if_missing(path: Path, text: str) -> None:
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")


def source_file(root: Path) -> Path | None:
    config = (root / "iteris.toml").read_text(encoding="utf-8", errors="replace") if (root / "iteris.toml").exists() else ""
    match = re.search(r'^source_file\s*=\s*"([^"]*)"', config, re.MULTILINE)
    if match and match.group(1):
        candidate = root / match.group(1)
        if candidate.exists():
            return candidate
    sources = sorted((root / "sources").glob("*"))
    return sources[0] if sources else None

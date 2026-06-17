"""Supervisor journal: append-only decision/action ledger plus sensor cursors.

The journal is the supervisor's behavior stream — observation digests,
decision summaries (with refs to raw agent runs), actions, and outcomes.
Decision entries form supersession chains: only the head of a chain is
"live", and liveness matters for audit navigation only. Journal content is
audit, never knowledge: it is excluded from memory search and prompts.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from iteris.project import append_jsonl, now_iso, now_stamp, read_json, slugify, write_json

JOURNAL_SCHEMA = "iteris.supervision_journal_line.v0"


def supervision_dir(project_root: Path) -> Path:
    return project_root / ".iteris" / "supervision"


def journal_path(project_root: Path) -> Path:
    return supervision_dir(project_root) / "journal.jsonl"


def cursors_path(project_root: Path) -> Path:
    return supervision_dir(project_root) / "cursors.json"


def append_entry(
    project_root: Path,
    *,
    entry_type: str,
    payload: dict[str, Any],
    supersedes: str | None = None,
    agent_run: str | None = None,
) -> dict[str, Any]:
    """Append one journal line and return it (including its generated id)."""
    entry = {
        "schema_version": JOURNAL_SCHEMA,
        "entry_id": f"jrnl-{now_stamp()}-{slugify(entry_type, 32)}",
        "ts": now_iso(),
        "entry_type": entry_type,
        "payload": payload,
    }
    if supersedes:
        entry["supersedes"] = supersedes
    if agent_run:
        entry["agent_run"] = agent_run
    append_jsonl(journal_path(project_root), entry)
    return entry


def read_entries(project_root: Path, *, limit: int | None = None) -> list[dict[str, Any]]:
    path = journal_path(project_root)
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(row, dict):
            rows.append(row)
    return rows[-limit:] if limit else rows


def live_decisions(project_root: Path, *, entry_type: str | None = None) -> list[dict[str, Any]]:
    """Heads of supersession chains: entries not superseded by a later entry."""
    rows = read_entries(project_root)
    superseded = {row.get("supersedes") for row in rows if row.get("supersedes")}
    heads = [row for row in rows if row.get("entry_id") not in superseded]
    if entry_type is not None:
        heads = [row for row in heads if row.get("entry_type") == entry_type]
    return heads


def load_cursors(project_root: Path) -> dict[str, Any]:
    return read_json(cursors_path(project_root), default={})


def save_cursors(project_root: Path, cursors: dict[str, Any]) -> None:
    write_json(cursors_path(project_root), cursors)

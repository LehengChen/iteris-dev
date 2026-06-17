"""Append-only scratch memory channels."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from iteris.project import append_jsonl, now_iso

CHANNELS = {"events", "observations", "failed_paths", "branch_states", "decisions"}


def append(project_root: Path, channel: str, record: dict[str, Any]) -> Path:
    if channel not in CHANNELS:
        raise ValueError(f"unknown scratch channel: {channel}")
    path = project_root / "memory" / "scratch" / f"{channel}.jsonl"
    entry = {"timestamp": now_iso(), "channel": channel, "record": record}
    append_jsonl(path, entry)
    if channel != "events":
        append_jsonl(
            project_root / "memory" / "scratch" / "events.jsonl",
            {"timestamp": now_iso(), "channel": "events", "record": {"event_type": "scratch_append", "channel": channel}},
        )
    return path


def append_event(project_root: Path, event_type: str, record: dict[str, Any] | None = None) -> Path:
    """Record a project event in the scratch event log using the canonical envelope."""
    payload = {"event_type": event_type, **(record or {})}
    return append(project_root, "events", payload)


def read_events(project_root: Path, *, limit: int | None = 5000) -> list[dict[str, Any]]:
    """Read scratch events normalized to flat ``{timestamp, event_type, ...}`` records.

    Historical logs mix two shapes: envelope records ``{timestamp, channel,
    record: {event_type, ...}}`` and flat records ``{timestamp, event_type, ...}``.
    All writers now emit the envelope; this reader accepts both so consumers
    never have to special-case the schema split again.

    ``limit`` keeps only the newest N lines before parsing — events.jsonl grows
    without bound on long runs and this is called from hot paths like
    ``iteris tool context``. Pass ``limit=None`` for a full read.
    """
    path = project_root / "memory" / "scratch" / "events.jsonl"
    if not path.exists():
        return []
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    if limit is not None and len(lines) > limit:
        lines = lines[-limit:]
    events: list[dict[str, Any]] = []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(row, dict):
            continue
        record = row.get("record")
        if isinstance(record, dict):
            flat = dict(record)
            flat.setdefault("timestamp", row.get("timestamp"))
        else:
            flat = dict(row)
        if flat.get("event_type"):
            events.append(flat)
    return events

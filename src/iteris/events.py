"""Structured project event log for agent and UI observation."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from iteris.project import append_jsonl, now_iso, now_stamp, slugify


def record_event(project_root: Path, event_type: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    root = project_root.resolve()
    event = {
        "schema_version": "iteris.event.v0",
        "event_id": f"event-{now_stamp()}-{slugify(event_type, 40)}",
        "event_type": event_type,
        "created_at": now_iso(),
        "project_path": str(root),
        "payload": payload or {},
    }
    append_jsonl(root / ".iteris" / "logs" / "events.jsonl", event)
    return event

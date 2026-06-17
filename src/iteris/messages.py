"""Structured supervisor->worker messaging with an explicit lifecycle.

A project's message state lives in two append-only files, preserving the
single-writer-per-file rule:

- ``messages/inbox.jsonl``  — written by senders outside the worker (the evolve
  supervisor; later a human CLI). The one sanctioned cross-project write.
- ``messages/ack.jsonl``    — written by this project's worker only.

Lifecycle: sent -> (listed by worker) -> acked(disposition). Escalation of
overdue high-priority messages is NOT implemented here: the supervisor's
message sensor calls :func:`unacked` and decides. This module is a pure
library plus thin queries — no engine dependency, no side effects beyond its
own two files.

Messages are transient directives, not knowledge: they are never merged into
memory search corpora or injected into prompts; the worker's resulting scratch
notes and tasks are the durable trace.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import json

from iteris.project import append_jsonl, now_iso, now_stamp, slugify

MESSAGE_SCHEMA = "iteris.message.v0"
ACK_SCHEMA = "iteris.message_ack.v0"

ALLOWED_TYPES = {"nudge", "hint", "question"}  # hint/question reserved for human senders
ALLOWED_PRIORITIES = {"normal", "high"}
ALLOWED_DISPOSITIONS = {"applied", "noted", "declined"}
ALLOWED_SENDERS = {"supervisor", "human"}

LIST_COMMAND = "iteris tool message list . --unread --json"


class MessageError(ValueError):
    """Raised for invalid message operations."""


def _messages_dir(project_root: Path) -> Path:
    return project_root / "messages"


def _inbox_path(project_root: Path) -> Path:
    return _messages_dir(project_root) / "inbox.jsonl"


def _ack_path(project_root: Path) -> Path:
    return _messages_dir(project_root) / "ack.jsonl"


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
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
    return rows


def send(
    project_root: Path,
    *,
    body: str,
    type: str = "nudge",
    priority: str = "normal",
    sender: str = "supervisor",
    refs: list[str] | None = None,
) -> dict[str, Any]:
    """Append a message to ``project_root``'s inbox and return the stored line."""
    if not body or not body.strip():
        raise MessageError("message body must be non-empty")
    if type not in ALLOWED_TYPES:
        raise MessageError(f"invalid message type: {type!r} (allowed: {sorted(ALLOWED_TYPES)})")
    if priority not in ALLOWED_PRIORITIES:
        raise MessageError(f"invalid priority: {priority!r} (allowed: {sorted(ALLOWED_PRIORITIES)})")
    if sender not in ALLOWED_SENDERS:
        raise MessageError(f"invalid sender: {sender!r} (allowed: {sorted(ALLOWED_SENDERS)})")
    msg_id = f"msg-{now_stamp()}-{slugify(body, 32) or 'message'}"
    message = {
        "schema_version": MESSAGE_SCHEMA,
        "msg_id": msg_id,
        "ts": now_iso(),
        "from": sender,
        "to": "worker",
        "type": type,
        "priority": priority,
        "body": body.strip(),
        "refs": refs or [],
    }
    append_jsonl(_inbox_path(project_root), message)
    return message


def ack(
    project_root: Path,
    *,
    msg_id: str,
    disposition: str,
    note: str = "",
) -> dict[str, Any]:
    """Record the worker's receipt/decision for one message."""
    if disposition not in ALLOWED_DISPOSITIONS:
        raise MessageError(
            f"invalid disposition: {disposition!r} (allowed: {sorted(ALLOWED_DISPOSITIONS)})"
        )
    known = {row.get("msg_id") for row in _load_jsonl(_inbox_path(project_root))}
    if msg_id not in known:
        raise MessageError(f"unknown msg_id: {msg_id}")
    already = {row.get("msg_id") for row in _load_jsonl(_ack_path(project_root))}
    if msg_id in already:
        raise MessageError(f"message already acked: {msg_id}")
    entry = {
        "schema_version": ACK_SCHEMA,
        "msg_id": msg_id,
        "ts": now_iso(),
        "by": "worker",
        "disposition": disposition,
        "note": note.strip(),
    }
    append_jsonl(_ack_path(project_root), entry)
    return entry


def list_messages(project_root: Path, *, unread_only: bool = False) -> list[dict[str, Any]]:
    """Merged inbox+ack view, oldest first. ``unread`` means not yet acked."""
    acks = {row.get("msg_id"): row for row in _load_jsonl(_ack_path(project_root))}
    merged: list[dict[str, Any]] = []
    for row in _load_jsonl(_inbox_path(project_root)):
        ack_row = acks.get(row.get("msg_id"))
        item = dict(row)
        item["acked"] = ack_row is not None
        if ack_row is not None:
            item["ack"] = {
                "ts": ack_row.get("ts"),
                "disposition": ack_row.get("disposition"),
                "note": ack_row.get("note", ""),
            }
        if unread_only and item["acked"]:
            continue
        merged.append(item)
    return merged


def _parse_ts(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def unacked(
    project_root: Path,
    *,
    min_priority: str | None = None,
    older_than_hours: float | None = None,
    now: datetime | None = None,
) -> list[dict[str, Any]]:
    """Unacked messages, optionally filtered by priority and age.

    This is the supervisor-side escalation query: ``unacked(min_priority="high",
    older_than_hours=N)`` returns the messages whose silence should trigger a
    re-send or a report entry.
    """
    if min_priority is not None and min_priority not in ALLOWED_PRIORITIES:
        raise MessageError(f"invalid priority filter: {min_priority!r}")
    rows = list_messages(project_root, unread_only=True)
    if min_priority == "high":
        rows = [row for row in rows if row.get("priority") == "high"]
    if older_than_hours is not None:
        reference = now or datetime.now(timezone.utc)
        kept: list[dict[str, Any]] = []
        for row in rows:
            ts = _parse_ts(row.get("ts"))
            if ts is None:
                kept.append(row)  # unparseable age counts as overdue, not invisible
                continue
            age_hours = (reference - ts).total_seconds() / 3600.0
            if age_hours >= older_than_hours:
                kept.append(row)
        rows = kept
    return rows


INLINE_HIGH_MESSAGE_LIMIT = 3
INLINE_BODY_CHARS = 700
ACK_COMMAND = "iteris tool message ack . --msg-id <msg-id> --disposition applied|noted|declined --json"


def unread_summary(project_root: Path) -> dict[str, Any] | None:
    """Compact piggyback payload for high-frequency tool outputs, or None.

    Returned (when there is anything unread) as ``unread_messages`` inside
    ``iteris tool context`` / ``task pool show`` JSON so a worker discovers
    pending guidance without polling a dedicated command.

    Unread HIGH-priority message bodies are inlined (oldest first, capped):
    supervision showed workers go hours without running the list command, so
    counts alone leave urgent operator directives unread. Inlining is still a
    discovery channel, not knowledge storage — the lifecycle stays
    discover (list or inline) -> act -> ack.
    """
    rows = list_messages(project_root, unread_only=True)
    if not rows:
        return None
    high_rows = [row for row in rows if row.get("priority") == "high"]
    inlined = []
    for row in high_rows[:INLINE_HIGH_MESSAGE_LIMIT]:
        body = str(row.get("body") or "")
        truncated = len(body) > INLINE_BODY_CHARS
        if truncated:
            body = body[:INLINE_BODY_CHARS] + " ... [truncated; run the list command for the full body]"
        inlined.append(
            {
                "msg_id": row.get("msg_id"),
                "ts": row.get("ts"),
                "from": row.get("from"),
                "type": row.get("type"),
                "body": body,
                "truncated": truncated,
                "refs": row.get("refs") or [],
            }
        )
    payload: dict[str, Any] = {
        "count": len(rows),
        "high": len(high_rows),
        "next": LIST_COMMAND,
    }
    if inlined:
        payload["high_messages"] = inlined
        payload["ack_command"] = ACK_COMMAND
        payload["guidance"] = (
            "Unread high-priority operator messages above: act on them now, then acknowledge each "
            "with the ack command. Do not start new routes while a high-priority directive is unread."
        )
    return payload

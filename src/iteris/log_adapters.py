"""Structured log adapters for dashboard streams.

Interactive goal loops and headless sub-agents write JSONL in executor-specific
formats. The dashboard should not know those raw formats; adapters discover the
right JSONL files and normalize each event into the shared LogEntry shape used
by the React UI.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Protocol
from xml.etree import ElementTree

from iteris.codex_logs import CodexEventNormalizer
from iteris.executors import EXECUTOR_CLAUDE, EXECUTOR_CODEX
from iteris.project import now_iso

LOG_ADAPTER_CODEX = EXECUTOR_CODEX
LOG_ADAPTER_CLAUDE = EXECUTOR_CLAUDE
KNOWN_LOG_ADAPTERS = (LOG_ADAPTER_CODEX, LOG_ADAPTER_CLAUDE)
NORMALIZE_RENDER_LIMIT = 4000


class EventNormalizer(Protocol):
    def feed(self, event: dict[str, Any]) -> dict[str, Any] | list[dict[str, Any]] | None:
        ...


@dataclass(frozen=True)
class LogAdapter:
    name: str
    label: str
    find_logs: Callable[[Path], list[Path]]
    normalizer_factory: Callable[[], EventNormalizer]


def _mtime_or_zero(path: Path) -> float:
    try:
        return path.stat().st_mtime
    except OSError:
        return 0.0


def _truncate(text: str, limit: int = NORMALIZE_RENDER_LIMIT) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n...[truncated {len(text) - limit} chars; see raw structured log]"


def find_codex_logs(home_dir: Path) -> list[Path]:
    sessions = home_dir / "sessions"
    if not sessions.is_dir():
        return []
    return sorted(sessions.glob("**/rollout-*.jsonl"), key=_mtime_or_zero, reverse=True)


def find_claude_logs(home_dir: Path) -> list[Path]:
    projects = home_dir / "projects"
    if not projects.is_dir():
        return []
    # Subagent sidechain transcripts (agent-*.jsonl) sit beside the main
    # transcript and can carry a newer mtime while a subagent is active;
    # the dashboard should always surface the main session transcript.
    logs = [path for path in projects.glob("**/*.jsonl") if not path.name.startswith("agent-")]
    return sorted(logs, key=_mtime_or_zero, reverse=True)


def resolve_log_adapter(value: str | None) -> LogAdapter:
    name = (value or LOG_ADAPTER_CODEX).strip().lower()
    if name not in ADAPTERS:
        raise ValueError(f"unknown log adapter {name!r}; expected one of {', '.join(KNOWN_LOG_ADAPTERS)}")
    return ADAPTERS[name]


def adapter_for_executor(executor: str | None) -> LogAdapter:
    name = (executor or LOG_ADAPTER_CODEX).strip().lower()
    if name == LOG_ADAPTER_CLAUDE:
        return ADAPTERS[LOG_ADAPTER_CLAUDE]
    return ADAPTERS[LOG_ADAPTER_CODEX]


def infer_log_adapter(relpath: str) -> str:
    normalized = relpath.replace("\\", "/")
    if normalized.endswith(".jsonl") and (normalized.startswith("projects/") or "/projects/" in normalized):
        return LOG_ADAPTER_CLAUDE
    return LOG_ADAPTER_CODEX


class ClaudeEventNormalizer:
    """Map Claude Code transcript JSONL into dashboard LogEntry dicts."""

    def __init__(self, *, clock: Any = None) -> None:
        self._clock = clock or now_iso
        self.session_id: str | None = None

    def _ts(self, event: dict[str, Any]) -> str:
        ts = event.get("timestamp")
        return str(ts) if ts else self._clock()

    def feed(self, event: dict[str, Any]) -> list[dict[str, Any]] | dict[str, Any] | None:
        if not isinstance(event, dict):
            return None
        # Transcript files use camelCase ``sessionId``; the headless
        # ``claude -p --output-format stream-json`` stream uses snake_case
        # ``session_id``. Accept either so one normalizer serves both the
        # interactive main loop and headless sub-agents/verifiers.
        session_id = event.get("sessionId") or event.get("session_id")
        if isinstance(session_id, str) and session_id:
            self.session_id = session_id

        event_type = str(event.get("type") or "")
        if event_type == "assistant":
            return self._assistant_entries(event)
        if event_type == "user":
            return self._user_entries(event)
        if event_type == "queue-operation":
            return self._queue_operation_entry(event)
        # Mode, permission-mode, file-history snapshots, and empty reminders are
        # useful in the raw transcript but too noisy for the primary log view.
        return None

    def _base(self, event: dict[str, Any], raw_type: str) -> dict[str, Any]:
        base = {"ts": self._ts(event), "raw_type": raw_type}
        if self.session_id:
            base["session_id"] = self.session_id
        request_id = event.get("requestId")
        if request_id:
            base["request_id"] = request_id
        return base

    def _assistant_entries(self, event: dict[str, Any]) -> list[dict[str, Any]]:
        message = event.get("message") if isinstance(event.get("message"), dict) else {}
        content = message.get("content")
        if not isinstance(content, list):
            return []
        entries: list[dict[str, Any]] = []
        model = message.get("model")
        usage = message.get("usage") if isinstance(message.get("usage"), dict) else {}
        for item in content:
            if not isinstance(item, dict):
                continue
            ctype = str(item.get("type") or "")
            base = self._base(event, f"assistant:{ctype}")
            if model:
                base["model"] = model
            if usage:
                base.update(_usage_fields(usage))
            if ctype == "text":
                text = str(item.get("text") or "")
                if text:
                    entries.append({**base, "event": "text", "content": text})
            elif ctype == "thinking":
                text = str(item.get("thinking") or "")
                if text:
                    rendered = _truncate(text)
                elif item.get("signature"):
                    rendered = "(thinking omitted; signature retained in raw transcript)"
                else:
                    rendered = "(thinking omitted)"
                entries.append({**base, "event": "thinking", "content": rendered})
            elif ctype == "tool_use":
                tool_id = str(item.get("id") or "")
                input_value = item.get("input") if isinstance(item.get("input"), dict) else {}
                entry = {
                    **base,
                    "event": "tool_call",
                    "tool": str(item.get("name") or "tool"),
                    "input": input_value,
                }
                if tool_id:
                    entry["tool_call_id"] = tool_id
                entries.append(entry)
        return entries

    def _user_entries(self, event: dict[str, Any]) -> list[dict[str, Any]]:
        if event.get("isMeta") is True:
            return []
        message = event.get("message") if isinstance(event.get("message"), dict) else {}
        content = message.get("content")
        if isinstance(content, str):
            return self._user_text_entry(event, content)
        if not isinstance(content, list):
            return []

        entries: list[dict[str, Any]] = []
        for item in content:
            if not isinstance(item, dict):
                continue
            if item.get("type") != "tool_result":
                continue
            base = self._base(event, "user:tool_result")
            tool_id = str(item.get("tool_use_id") or "")
            entry = {
                **base,
                "event": "tool_result",
                "level": "error" if item.get("is_error") else "info",
                "content": _truncate(_content_text(item.get("content"))),
            }
            if tool_id:
                entry["tool_call_id"] = tool_id
            entries.append(entry)
        return entries

    def _user_text_entry(self, event: dict[str, Any], text: str) -> list[dict[str, Any]]:
        if not text.strip():
            return []
        if text.startswith("<local-command-stdout>"):
            return []
        base = self._base(event, "user:message")
        if text.startswith("<command-name>/goal</command-name>"):
            return [{**base, "event": "prompt", "content": _truncate(text)}]
        return [{**base, "event": "text", "content": _truncate(text)}]

    def _queue_operation_entry(self, event: dict[str, Any]) -> dict[str, Any] | None:
        content = _content_text(event.get("content"))
        if not content.strip():
            return None
        operation = str(event.get("operation") or "unknown")
        entry = {
            **self._base(event, f"queue-operation:{operation}"),
            "event": "shell",
            "level": "info",
            "content": _truncate(content),
        }
        summary = _notification_summary(content)
        if summary:
            entry["message"] = summary
        return entry


def _usage_fields(usage: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    input_tokens = usage.get("input_tokens")
    output_tokens = usage.get("output_tokens")
    if input_tokens is not None:
        out["input_tokens"] = input_tokens
    if output_tokens is not None:
        out["output_tokens"] = output_tokens
    return out


def _content_text(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        parts: list[str] = []
        for item in value:
            if isinstance(item, dict):
                parts.append(str(item.get("text") or item.get("content") or ""))
            else:
                parts.append(str(item))
        return "\n".join(part for part in parts if part)
    return "" if value is None else str(value)


def _notification_summary(content: str) -> str | None:
    try:
        root = ElementTree.fromstring(content)
    except ElementTree.ParseError:
        return None
    summary = root.findtext("summary")
    if summary:
        return summary.strip()
    return None


ADAPTERS: dict[str, LogAdapter] = {
    LOG_ADAPTER_CODEX: LogAdapter(
        name=LOG_ADAPTER_CODEX,
        label="Codex",
        find_logs=find_codex_logs,
        normalizer_factory=CodexEventNormalizer,
    ),
    LOG_ADAPTER_CLAUDE: LogAdapter(
        name=LOG_ADAPTER_CLAUDE,
        label="Claude",
        find_logs=find_claude_logs,
        normalizer_factory=ClaudeEventNormalizer,
    ),
}


def normalize_structured_file(
    path: Path,
    *,
    adapter: str | None = None,
    max_bytes: int | None = None,
) -> list[dict[str, Any]]:
    normalizer = resolve_log_adapter(adapter).normalizer_factory()
    entries: list[dict[str, Any]] = []
    if not path.exists():
        return entries

    def _feed(line: str) -> None:
        line = line.strip()
        if not line:
            return
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            return
        mapped = normalizer.feed(event)
        if mapped is None:
            return
        if isinstance(mapped, list):
            entries.extend(mapped)
        else:
            entries.append(mapped)

    if max_bytes is None:
        with path.open("r", encoding="utf-8", errors="replace", newline="\n") as handle:
            for line in handle:
                _feed(line)
        return entries

    with path.open("rb") as handle:
        data = handle.read(max_bytes)
    if len(data) == max_bytes and not data.endswith(b"\n"):
        head, _, _ = data.rpartition(b"\n")
        data = head
    for line in data.decode("utf-8", errors="replace").split("\n"):
        _feed(line)
    return entries


def normalize_event_line(event: dict[str, Any], *, adapter: str | None = None) -> list[dict[str, Any]]:
    normalizer = resolve_log_adapter(adapter).normalizer_factory()
    mapped = normalizer.feed(event)
    if mapped is None:
        return []
    return mapped if isinstance(mapped, list) else [mapped]


def _indent(text: str) -> str:
    return "\n".join(f"  {line}" for line in text.splitlines()) if text else "  "


def _format_log_entry(line_no: int, entry: dict[str, Any]) -> str:
    kind = str(entry.get("event") or "?")
    parts = [f"\n[{line_no}] {kind}"]
    tool = entry.get("tool")
    if tool:
        parts.append(f"  tool: {tool}")
    if entry.get("input") is not None:
        parts.append(_indent(_truncate(json.dumps(entry["input"], ensure_ascii=False, separators=(",", ":")))))
    content = entry.get("content")
    if content:
        parts.append(_indent(_truncate(str(content))))
    return "\n".join(parts) + "\n"


def render_claude_events(
    events_path: Path,
    text_log_path: Path,
    *,
    header_lines: list[str] | None = None,
) -> dict[str, Any]:
    """Render Claude headless ``stream-json`` events into a human-readable log.

    Mirrors ``codex_logs.render_codex_events``' contract — same signature and
    same ``{event_count, malformed_line_count, session_id}`` return — so the
    executor-agnostic headless runner can dispatch on it. The raw JSONL is left
    untouched in ``events_path``; this only writes the compact text view.
    """
    normalizer = ClaudeEventNormalizer()
    event_count = 0
    malformed_line_count = 0
    text_log_path.parent.mkdir(parents=True, exist_ok=True)
    with text_log_path.open("w", encoding="utf-8") as out:
        for line in header_lines or []:
            out.write(line.rstrip() + "\n")
        out.write("--- rendered claude stream-json events ---\n")
        if not events_path.exists():
            out.write("[missing events file]\n")
            return {"event_count": 0, "malformed_line_count": 0, "session_id": None}
        # Split on '\n' only (never splitlines): JSON strings may contain raw
        # U+2028/U+2029, which splitlines() would break on.
        with events_path.open("r", encoding="utf-8", errors="replace", newline="\n") as handle:
            for line_no, raw in enumerate(handle, start=1):
                raw = raw.rstrip("\n")
                if not raw.strip():
                    continue
                event_count += 1
                try:
                    event = json.loads(raw)
                except json.JSONDecodeError:
                    malformed_line_count += 1
                    out.write(f"\n[{line_no}] raw: {_truncate(raw)}\n")
                    continue
                mapped = normalizer.feed(event)
                entries = mapped if isinstance(mapped, list) else ([mapped] if mapped else [])
                for entry in entries:
                    out.write(_format_log_entry(line_no, entry))
    return {
        "event_count": event_count,
        "malformed_line_count": malformed_line_count,
        "session_id": normalizer.session_id,
    }

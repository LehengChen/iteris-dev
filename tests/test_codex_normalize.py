from __future__ import annotations

import json

from iteris.codex_logs import CodexEventNormalizer, normalize_codex_file


def test_normalize_format_a_thread_turn_item():
    n = CodexEventNormalizer()
    # thread.started / turn.started carry no display value.
    assert n.feed({"type": "thread.started", "thread_id": "t1"}) is None
    assert n.session_id == "t1"
    assert n.feed({"type": "turn.started"}) is None

    msg = n.feed({"type": "item.completed", "item": {"type": "agent_message", "text": "hi"}})
    assert msg["event"] == "text"
    assert msg["content"] == "hi"

    call = n.feed(
        {
            "type": "item.started",
            "item": {"type": "command_execution", "command": "/bin/bash -lc 'ls'", "status": "in_progress"},
        }
    )
    assert call["event"] == "tool_call"
    assert call["tool"] == "Bash"
    assert call["input"]["command"] == "/bin/bash -lc 'ls'"

    result = n.feed(
        {
            "type": "item.completed",
            "item": {
                "type": "command_execution",
                "command": "/bin/bash -lc 'ls'",
                "aggregated_output": "a\nb\n",
                "exit_code": 1,
                "status": "completed",
            },
        }
    )
    assert result["event"] == "tool_result"
    assert result["level"] == "error"  # non-zero exit
    assert "a" in result["content"]

    end = n.feed({"type": "turn.completed", "usage": {"input_tokens": 10, "output_tokens": 3}})
    assert end["event"] == "session_end"
    assert end["input_tokens"] == 10


def test_normalize_format_b_session_payload():
    n = CodexEventNormalizer()
    assert n.feed({"type": "session_meta", "payload": {"type": "session_meta", "id": "s9"}}) is None
    assert n.session_id == "s9"

    reasoning = n.feed(
        {"timestamp": "2026-01-01T00:00:00Z", "type": "event", "payload": {"type": "reasoning", "summary": ["step"]}}
    )
    assert reasoning["event"] == "thinking"
    assert reasoning["ts"] == "2026-01-01T00:00:00Z"

    fc = n.feed({"type": "event", "payload": {"type": "function_call", "name": "search", "arguments": "{}"}})
    assert fc["event"] == "tool_call"
    assert fc["tool"] == "search"

    # token_count is low-signal and dropped.
    assert n.feed({"type": "event", "payload": {"type": "token_count", "info": {}}}) is None


def test_normalize_rollout_drops_message_noise_and_duplicates():
    """Interactive rollout `message` items are noise or duplicate agent_message."""
    n = CodexEventNormalizer()

    # developer permissions block + user environment/prompt echo → dropped.
    assert n.feed({"type": "response_item", "payload": {"type": "message", "role": "developer",
                   "content": [{"text": "<permissions instructions>..."}]}}) is None
    assert n.feed({"type": "response_item", "payload": {"type": "message", "role": "user",
                   "content": [{"text": "<environment_context>..."}]}}) is None

    # The real user prompt arrives via user_message (kept).
    um = n.feed({"type": "event_msg", "payload": {"type": "user_message", "message": "do the thing"}})
    assert um["event"] == "text" and um["content"] == "do the thing"

    # Assistant turn: agent_message is kept; the duplicate `message` item is dropped.
    am = n.feed({"type": "event_msg", "payload": {"type": "agent_message", "message": "done"}})
    assert am["event"] == "text" and am["content"] == "done"
    assert n.feed({"type": "response_item", "payload": {"type": "message", "role": "assistant",
                   "content": [{"text": "done"}]}}) is None


def test_normalize_codex_file(tmp_path):
    path = tmp_path / "codex.events.jsonl"
    rows = [
        {"type": "thread.started", "thread_id": "t1"},
        {"type": "item.completed", "item": {"type": "agent_message", "text": "hello"}},
        "not json",
        {"type": "item.completed", "item": {"type": "command_execution", "command": "echo x", "status": "completed", "exit_code": 0, "aggregated_output": "x"}},
    ]
    path.write_text("\n".join(json.dumps(r) if isinstance(r, dict) else r for r in rows) + "\n", encoding="utf-8")
    entries = normalize_codex_file(path)
    # thread.started dropped, malformed line skipped, 2 real entries remain.
    assert [e["event"] for e in entries] == ["text", "tool_result"]


def test_normalize_codex_file_keeps_unicode_line_separators(tmp_path):
    """JSON strings may contain raw U+2028/U+2029; record splitting must not break on them."""
    path = tmp_path / "codex.events.jsonl"
    text = "before after end"
    line = json.dumps({"type": "item.completed", "item": {"type": "agent_message", "text": text}}, ensure_ascii=False)
    path.write_text(line + "\n", encoding="utf-8")

    entries = normalize_codex_file(path)
    assert [e["content"] for e in entries] == [text]
    # Same contract on the byte-bounded path.
    entries = normalize_codex_file(path, max_bytes=len((line + "\n").encode()))
    assert [e["content"] for e in entries] == [text]


def test_normalize_codex_file_max_bytes_drops_partial_line(tmp_path):
    path = tmp_path / "codex.events.jsonl"
    line1 = json.dumps({"type": "item.completed", "item": {"type": "agent_message", "text": "one"}}) + "\n"
    line2 = json.dumps({"type": "item.completed", "item": {"type": "agent_message", "text": "two"}}) + "\n"
    path.write_text(line1 + line2, encoding="utf-8")

    # Cut inside line2: only the complete line1 is processed.
    cut = len(line1.encode()) + 5
    entries = normalize_codex_file(path, max_bytes=cut)
    assert [e["content"] for e in entries] == ["one"]

    # Cut exactly at the line1 boundary: identical result, nothing dropped.
    entries = normalize_codex_file(path, max_bytes=len(line1.encode()))
    assert [e["content"] for e in entries] == ["one"]

    # No limit: both lines.
    entries = normalize_codex_file(path)
    assert [e["content"] for e in entries] == ["one", "two"]

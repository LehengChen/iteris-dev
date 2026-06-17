from __future__ import annotations

import json
import os

from iteris.log_adapters import ClaudeEventNormalizer, find_claude_logs, infer_log_adapter, normalize_structured_file


def test_claude_normalizer_maps_text_tool_call_and_result():
    n = ClaudeEventNormalizer()

    text = n.feed(
        {
            "type": "assistant",
            "sessionId": "s1",
            "timestamp": "2026-01-01T00:00:00Z",
            "requestId": "req-1",
            "message": {
                "model": "claude-fable-5",
                "role": "assistant",
                "content": [{"type": "text", "text": "I will inspect the project."}],
                "usage": {"input_tokens": 10, "output_tokens": 3},
            },
        }
    )
    assert text == [
        {
            "ts": "2026-01-01T00:00:00Z",
            "raw_type": "assistant:text",
            "session_id": "s1",
            "request_id": "req-1",
            "model": "claude-fable-5",
            "input_tokens": 10,
            "output_tokens": 3,
            "event": "text",
            "content": "I will inspect the project.",
        }
    ]

    call = n.feed(
        {
            "type": "assistant",
            "sessionId": "s1",
            "timestamp": "2026-01-01T00:00:01Z",
            "message": {
                "role": "assistant",
                "content": [
                    {
                        "type": "tool_use",
                        "id": "toolu-1",
                        "name": "Bash",
                        "input": {"command": "iteris status . --json"},
                    }
                ],
            },
        }
    )
    assert call == [
        {
            "ts": "2026-01-01T00:00:01Z",
            "raw_type": "assistant:tool_use",
            "session_id": "s1",
            "event": "tool_call",
            "tool": "Bash",
            "input": {"command": "iteris status . --json"},
            "tool_call_id": "toolu-1",
        }
    ]

    result = n.feed(
        {
            "type": "user",
            "sessionId": "s1",
            "timestamp": "2026-01-01T00:00:02Z",
            "message": {
                "role": "user",
                "content": [
                    {
                        "tool_use_id": "toolu-1",
                        "type": "tool_result",
                        "content": "ok",
                        "is_error": False,
                    }
                ],
            },
        }
    )
    assert result == [
        {
            "ts": "2026-01-01T00:00:02Z",
            "raw_type": "user:tool_result",
            "session_id": "s1",
            "event": "tool_result",
            "level": "info",
            "content": "ok",
            "tool_call_id": "toolu-1",
        }
    ]


def test_claude_normalizer_drops_noisy_state_events():
    n = ClaudeEventNormalizer()
    assert n.feed({"type": "permission-mode", "permissionMode": "bypassPermissions"}) is None
    assert n.feed({"type": "file-history-snapshot", "snapshot": {}}) is None
    assert n.feed({"type": "user", "isMeta": True, "message": {"content": "hook text"}}) == []


def test_claude_normalizer_labels_signature_only_thinking():
    n = ClaudeEventNormalizer()
    entries = n.feed(
        {
            "type": "assistant",
            "sessionId": "s1",
            "timestamp": "2026-01-01T00:00:01Z",
            "message": {
                "role": "assistant",
                "content": [{"type": "thinking", "thinking": "", "signature": "signed"}],
            },
        }
    )

    assert entries == [
        {
            "ts": "2026-01-01T00:00:01Z",
            "raw_type": "assistant:thinking",
            "session_id": "s1",
            "event": "thinking",
            "content": "(thinking omitted; signature retained in raw transcript)",
        }
    ]


def test_claude_normalizer_maps_queue_operation_notification():
    n = ClaudeEventNormalizer()
    entry = n.feed(
        {
            "type": "queue-operation",
            "operation": "enqueue",
            "timestamp": "2026-01-01T00:00:03Z",
            "sessionId": "s1",
            "content": (
                "<task-notification>\n"
                "<task-id>task-1</task-id>\n"
                '<summary>Background command "verify facts" completed (exit code 0)</summary>\n'
                "</task-notification>"
            ),
        }
    )

    assert entry == {
        "ts": "2026-01-01T00:00:03Z",
        "raw_type": "queue-operation:enqueue",
        "session_id": "s1",
        "event": "shell",
        "level": "info",
        "message": 'Background command "verify facts" completed (exit code 0)',
        "content": (
            "<task-notification>\n"
            "<task-id>task-1</task-id>\n"
            '<summary>Background command "verify facts" completed (exit code 0)</summary>\n'
            "</task-notification>"
        ),
    }
    assert n.feed({"type": "queue-operation", "operation": "remove", "sessionId": "s1"}) is None


def test_infer_log_adapter_detects_claude_project_transcripts():
    assert infer_log_adapter(".iteris/codex_home/run/projects/-tmp/session.jsonl") == "claude"
    assert infer_log_adapter("projects/-tmp/session.jsonl") == "claude"
    assert infer_log_adapter(".iteris/codex_home/run/sessions/2026/01/rollout.jsonl") == "codex"


def test_find_claude_logs_skips_subagent_sidechain_transcripts(tmp_path):
    """agent-*.jsonl sidechains can be newer than the main transcript while a
    subagent runs; the dashboard must keep surfacing the main session."""
    project_dir = tmp_path / "projects" / "-tmp-proj"
    project_dir.mkdir(parents=True)
    main = project_dir / "session.jsonl"
    main.write_text("{}\n", encoding="utf-8")
    sidechain = project_dir / "agent-abc123.jsonl"
    sidechain.write_text("{}\n", encoding="utf-8")
    past = main.stat().st_mtime - 60
    os.utime(main, (past, past))  # make the sidechain strictly newer

    assert find_claude_logs(tmp_path) == [main]


def test_normalize_structured_file_with_claude_adapter(tmp_path):
    path = tmp_path / "session.jsonl"
    rows = [
        {"type": "mode", "mode": "normal", "sessionId": "s1"},
        {
            "type": "assistant",
            "sessionId": "s1",
            "timestamp": "2026-01-01T00:00:00Z",
            "message": {"role": "assistant", "content": [{"type": "text", "text": "hello"}]},
        },
        {
            "type": "assistant",
            "sessionId": "s1",
            "timestamp": "2026-01-01T00:00:01Z",
            "message": {"role": "assistant", "content": [{"type": "thinking", "thinking": ""}]},
        },
    ]
    path.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")

    entries = normalize_structured_file(path, adapter="claude")
    assert [e["event"] for e in entries] == ["text", "thinking"]
    assert entries[0]["content"] == "hello"
    assert entries[1]["content"] == "(thinking omitted)"

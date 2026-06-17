from __future__ import annotations

import json

from iteris.codex_logs import render_codex_events


def test_render_current_codex_exec_json_schema(tmp_path):
    events = tmp_path / "codex.events.jsonl"
    text_log = tmp_path / "codex.log"
    rows = [
        {"type": "thread.started", "thread_id": "019e985c-6050-7712-b06d-68dd163f4881"},
        {"type": "turn.started"},
        {"type": "item.completed", "item": {"id": "item_0", "type": "agent_message", "text": "I inspected the route."}},
        {
            "type": "item.completed",
            "item": {
                "id": "item_1",
                "type": "command_execution",
                "command": "/bin/bash -lc 'echo ok'",
                "aggregated_output": "ok\n",
                "exit_code": 0,
                "status": "completed",
            },
        },
        {"type": "turn.completed", "usage": {"input_tokens": 10, "output_tokens": 3}},
    ]
    events.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")

    result = render_codex_events(events, text_log)

    rendered = text_log.read_text(encoding="utf-8")
    assert result["session_id"] == "019e985c-6050-7712-b06d-68dd163f4881"
    assert result["event_count"] == 5
    assert result["malformed_line_count"] == 0
    assert "thread_id: 019e985c-6050-7712-b06d-68dd163f4881" in rendered
    assert "item.completed:agent_message" in rendered
    assert "I inspected the route." in rendered
    assert "/bin/bash -lc 'echo ok'" in rendered
    assert '"input_tokens":10' in rendered

"""Tests for iteris monitor command."""

from __future__ import annotations

import json

from typer.testing import CliRunner

from iteris.cli import app


def test_monitor_setup_fails_without_executor(tmp_path, monkeypatch):
    monkeypatch.setattr("shutil.which", lambda _name: None)
    result = CliRunner().invoke(app, ["monitor", str(tmp_path)])
    assert result.exit_code != 0


def test_monitor_welcome_only_json(tmp_path, monkeypatch):
    monkeypatch.setattr("shutil.which", lambda name: f"/usr/bin/{name}" if name in {"codex", "git", "rg"} else None)
    result = CliRunner().invoke(
        app,
        ["monitor", str(tmp_path), "--no-setup", "--json", "--welcome-only", "-e", "codex", "--lang", "en"],
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert "welcome" in payload
    assert "Welcome to Iteris Monitor" in payload["welcome"]
    assert payload.get("locale") == "en"
    assert "menu" in payload


def test_monitor_message_json_writes_handoff(tmp_path, monkeypatch):
    monkeypatch.setattr("shutil.which", lambda name: f"/usr/bin/{name}" if name in {"codex", "git", "rg"} else None)
    result = CliRunner().invoke(
        app,
        ["monitor", str(tmp_path), "--no-setup", "--json", "-m", "how do I start?", "-e", "codex"],
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["user"] == "how do I start?"
    assert payload["executor"] == "codex"
    assert payload["command"][0].endswith("codex")
    assert "--yolo" in payload["command"]
    assert "assistant" not in payload
    handoff = tmp_path / ".iteris" / "monitor" / "handoff.md"
    assert handoff.exists()
    handoff_text = handoff.read_text(encoding="utf-8")
    assert "how do I start?" in handoff_text
    assert payload["command"][-1] == handoff_text
    assert not payload["command"][-1].startswith("Read `")


def test_monitor_claude_message_json_uses_dangerously_skip_permissions(tmp_path, monkeypatch):
    monkeypatch.setattr("shutil.which", lambda name: f"/usr/bin/{name}" if name in {"claude", "git", "rg"} else None)
    result = CliRunner().invoke(
        app,
        ["monitor", str(tmp_path), "--no-setup", "--json", "-m", "how do I start?", "-e", "claude"],
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["executor"] == "claude"
    assert payload["command"][0].endswith("claude")
    assert "--dangerously-skip-permissions" in payload["command"]
    assert payload["env_updates"]["IS_SANDBOX"] == "1"
    handoff = tmp_path / ".iteris" / "monitor" / "handoff.md"
    assert payload["command"][-1] == handoff.read_text(encoding="utf-8")
    assert not payload["command"][-1].startswith("Read `")


def test_monitor_claude_launch_passes_sandbox_env(tmp_path, monkeypatch):
    captured = {}

    class Result:
        returncode = 0

    def fake_run(command, **kwargs):
        captured["command"] = command
        captured["kwargs"] = kwargs
        return Result()

    monkeypatch.setattr("shutil.which", lambda name: f"/usr/bin/{name}" if name in {"claude", "git", "rg"} else None)
    monkeypatch.setattr("iteris.commands.monitor.subprocess.run", fake_run)
    result = CliRunner().invoke(
        app,
        ["monitor", str(tmp_path), "--no-setup", "-m", "how do I start?", "-e", "claude", "--lang", "en"],
    )

    assert result.exit_code == 0, result.output
    assert "--dangerously-skip-permissions" in captured["command"]
    assert captured["kwargs"]["env"]["IS_SANDBOX"] == "1"


def test_monitor_long_handoff_uses_file_reference_in_initial_message(tmp_path, monkeypatch):
    monkeypatch.setattr("shutil.which", lambda name: f"/usr/bin/{name}" if name in {"codex", "git", "rg"} else None)
    long_handoff = "HEADER\n" + ("x" * 13000) + "\n# USER MESSAGE\nhow do I start?\n"
    monkeypatch.setattr("iteris.commands.monitor.build_monitor_handoff", lambda **_kwargs: long_handoff)
    result = CliRunner().invoke(
        app,
        ["monitor", str(tmp_path), "--no-setup", "--json", "-m", "how do I start?", "-e", "codex"],
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    handoff = tmp_path / ".iteris" / "monitor" / "handoff.md"
    assert handoff.read_text(encoding="utf-8") == long_handoff
    assert payload["initial_message"] == payload["command"][-1]
    assert payload["initial_message"] != long_handoff
    assert len(payload["initial_message"]) < 1000
    assert "完整 handoff 文件：" in payload["initial_message"]
    assert "未读取前不要回答用户问题" in payload["initial_message"]
    assert "how do I start?" in payload["initial_message"]


def test_monitor_long_handoff_file_reference_follows_english_locale(tmp_path, monkeypatch):
    monkeypatch.setattr("shutil.which", lambda name: f"/usr/bin/{name}" if name in {"codex", "git", "rg"} else None)
    long_handoff = "HEADER\n" + ("x" * 13000) + "\n# USER MESSAGE\nhow do I start?\n"
    monkeypatch.setattr("iteris.commands.monitor.build_monitor_handoff", lambda **_kwargs: long_handoff)
    result = CliRunner().invoke(
        app,
        ["monitor", str(tmp_path), "--no-setup", "--json", "-m", "how do I start?", "-e", "codex", "--lang", "en"],
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert "Full handoff file:" in payload["initial_message"]
    assert "Do not answer the user until you have read it." in payload["initial_message"]


def test_monitor_prefers_available_claude_when_codex_missing(tmp_path, monkeypatch):
    monkeypatch.delenv("ITERIS_EXECUTOR", raising=False)
    monkeypatch.setattr("shutil.which", lambda name: f"/usr/bin/{name}" if name in {"claude", "git", "rg"} else None)
    result = CliRunner().invoke(
        app,
        ["monitor", str(tmp_path), "--json", "--welcome-only", "--lang", "en"],
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["executor"] == "claude"


def test_package_data_guide_index_readable():
    from iteris.guide.index import framework_guide_index_text

    text = framework_guide_index_text()
    assert "iteris monitor" in text
    assert "Scene routing" in text or "Scene routing" in text

from __future__ import annotations

import json
from pathlib import Path

import pytest

from iteris.commands.goal import build_codex_command, build_shell_command
from iteris.executors import (
    build_claude_command,
    claude_state_file,
    ensure_claude_project_trusted,
    main_agent_home_env,
    prepare_claude_home,
    resolve_executor,
)


def test_resolve_executor_defaults_to_codex():
    assert resolve_executor(None, env={}) == "codex"


def test_resolve_executor_reads_env_and_explicit_value_wins():
    assert resolve_executor(None, env={"ITERIS_EXECUTOR": "claude"}) == "claude"
    assert resolve_executor("codex", env={"ITERIS_EXECUTOR": "claude"}) == "codex"
    assert resolve_executor("Claude", env={}) == "claude"


def test_resolve_executor_rejects_unknown_names():
    with pytest.raises(ValueError):
        resolve_executor("gemini", env={})


def test_build_claude_command_maps_yolo_to_skip_permissions(tmp_path):
    prompt = "/goal Read `.iteris/goal_prompt.txt` first."
    cmd = build_claude_command(tmp_path, prompt, yolo=True)
    assert cmd == ["claude", "--dangerously-skip-permissions", prompt]
    # Claude Code has no --cd flag; cwd comes from the shell command.
    assert "--cd" not in cmd


def test_build_claude_command_without_yolo_and_with_model(tmp_path):
    cmd = build_claude_command(tmp_path, "hi", executable="/bin/claude", yolo=False, model="claude-opus-4-8")
    assert cmd == ["/bin/claude", "--model", "claude-opus-4-8", "hi"]


def test_build_codex_command_accepts_model(tmp_path):
    cmd = build_codex_command(tmp_path, "hi", model="gpt-5.5")
    assert cmd[:5] == ["codex", "--yolo", "--no-alt-screen", "-m", "gpt-5.5"]


def test_main_agent_home_env_per_executor(tmp_path):
    assert main_agent_home_env("codex", tmp_path) == {"CODEX_HOME": str(tmp_path)}
    claude_env = main_agent_home_env("claude", tmp_path)
    assert claude_env["CLAUDE_CONFIG_DIR"] == str(tmp_path)
    assert claude_env["IS_SANDBOX"] == "1"
    # Suppress the interactive modals (feedback survey, auto-updater) that froze
    # unattended runs; the codex branch is unaffected.
    assert claude_env["CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC"] == "1"
    assert "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC" not in main_agent_home_env("codex", tmp_path)


def test_main_agent_home_env_modal_suppression_override(tmp_path, monkeypatch):
    monkeypatch.setenv("ITERIS_CLAUDE_DISABLE_NONESSENTIAL_TRAFFIC", "0")
    assert main_agent_home_env("claude", tmp_path)["CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC"] == "0"


def test_build_shell_command_env_updates(tmp_path):
    root = tmp_path / "project"
    root.mkdir()
    cmd = build_claude_command(root, yolo=True, prompt_argument=False)
    shell_cmd = build_shell_command(root, cmd, env_updates={"CLAUDE_CONFIG_DIR": str(tmp_path / "home"), "IS_SANDBOX": "1"})
    assert "exec env" in shell_cmd
    assert "IS_SANDBOX=1" in shell_cmd
    assert f"CLAUDE_CONFIG_DIR={tmp_path / 'home'}" in shell_cmd
    assert "claude --dangerously-skip-permissions" in shell_cmd


def test_prepare_claude_home_symlinks_auth_and_state(tmp_path):
    source = tmp_path / "real-claude"
    source.mkdir()
    (source / ".credentials.json").write_text("{}", encoding="utf-8")
    (source / "settings.json").write_text("{}", encoding="utf-8")
    (source / ".claude.json").write_text("{}", encoding="utf-8")
    home_dir = tmp_path / "per-run"

    prepare_claude_home(home_dir, env={"CLAUDE_CONFIG_DIR": str(source)})

    assert (home_dir / ".credentials.json").is_symlink()
    assert (home_dir / "settings.json").is_symlink()
    assert (home_dir / ".claude.json").is_symlink()
    assert (home_dir / ".claude.json").resolve() == (source / ".claude.json").resolve()
    # Idempotent and tolerant of missing sources.
    prepare_claude_home(home_dir, env={"CLAUDE_CONFIG_DIR": str(source)})
    prepare_claude_home(tmp_path / "fresh", env={"CLAUDE_CONFIG_DIR": str(tmp_path / "missing")})


def test_claude_state_file_location(tmp_path):
    assert claude_state_file(env={"CLAUDE_CONFIG_DIR": str(tmp_path)}) == tmp_path / ".claude.json"
    assert claude_state_file(env={}) == Path.home() / ".claude.json"


def test_ensure_claude_project_trusted_writes_state(tmp_path):
    config_dir = tmp_path / "claude-home"
    project = tmp_path / "project"
    project.mkdir()
    env = {"CLAUDE_CONFIG_DIR": str(config_dir)}

    result = ensure_claude_project_trusted(project, env=env)
    state = json.loads((config_dir / ".claude.json").read_text(encoding="utf-8"))
    assert result["updated"] is True
    assert state["projects"][str(project.resolve())]["hasTrustDialogAccepted"] is True

    second = ensure_claude_project_trusted(project, env=env)
    assert second["already_trusted"] is True
    assert second["updated"] is False


def test_ensure_claude_project_trusted_leaves_unreadable_state_untouched(tmp_path):
    """A mid-rewrite (corrupt) global state file must never be rebuilt from scratch."""
    config_dir = tmp_path / "claude-home"
    config_dir.mkdir()
    project = tmp_path / "project"
    project.mkdir()
    corrupt = '{"hasCompletedOnboarding": true, "projects": {'  # truncated JSON
    (config_dir / ".claude.json").write_text(corrupt, encoding="utf-8")
    env = {"CLAUDE_CONFIG_DIR": str(config_dir)}

    result = ensure_claude_project_trusted(project, env=env)

    assert result["updated"] is False
    assert "left untouched" in str(result["error"])
    assert (config_dir / ".claude.json").read_text(encoding="utf-8") == corrupt


def test_ensure_claude_project_trusted_writes_through_symlink(tmp_path):
    """Per-run homes symlink .claude.json to the real state file; the trust
    edit must land in the target, not replace the symlink with a copy."""
    real = tmp_path / "real-home"
    real.mkdir()
    (real / ".claude.json").write_text("{}", encoding="utf-8")
    run_home = tmp_path / "per-run"
    run_home.mkdir()
    (run_home / ".claude.json").symlink_to(real / ".claude.json")
    project = tmp_path / "project"
    project.mkdir()

    ensure_claude_project_trusted(project, env={"CLAUDE_CONFIG_DIR": str(run_home)})

    assert (run_home / ".claude.json").is_symlink()
    state = json.loads((real / ".claude.json").read_text(encoding="utf-8"))
    assert state["projects"][str(project.resolve())]["hasTrustDialogAccepted"] is True


def test_ensure_claude_project_trusted_preserves_existing_state(tmp_path):
    config_dir = tmp_path / "claude-home"
    config_dir.mkdir()
    project = tmp_path / "project"
    project.mkdir()
    (config_dir / ".claude.json").write_text(
        json.dumps({"hasCompletedOnboarding": True, "projects": {"/other": {"hasTrustDialogAccepted": True}}}),
        encoding="utf-8",
    )
    env = {"CLAUDE_CONFIG_DIR": str(config_dir)}

    ensure_claude_project_trusted(project, env=env)

    state = json.loads((config_dir / ".claude.json").read_text(encoding="utf-8"))
    assert state["hasCompletedOnboarding"] is True
    assert state["projects"]["/other"]["hasTrustDialogAccepted"] is True
    assert state["projects"][str(project.resolve())]["hasTrustDialogAccepted"] is True

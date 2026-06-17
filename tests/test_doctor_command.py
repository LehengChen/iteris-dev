from __future__ import annotations

import json
import subprocess
from pathlib import Path

from typer.testing import CliRunner

import iteris.commands.doctor as doctor_command
from iteris.cli import app
from iteris.project import init_project


def test_doctor_json_reports_missing_run_dependencies(monkeypatch):
    def fake_which(binary: str) -> str | None:
        if binary == "apt-get":
            return "/usr/bin/apt-get"
        if binary in {"git", "rg", "tmux", "codex"}:
            return None
        return f"/usr/bin/{binary}"

    monkeypatch.setattr(doctor_command.platform, "system", lambda: "Linux")
    monkeypatch.setattr(doctor_command.shutil, "which", fake_which)

    result = CliRunner().invoke(app, ["doctor", "--json"])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["schema_version"] == "iteris.doctor.v0"
    assert payload["ready_for_run"] is False
    assert payload["ok"] is False
    hints = "\n".join(payload["hints"])
    assert "sudo apt-get update" in hints
    assert "ripgrep" in hints
    assert "npm install -g @openai/codex" in hints


def test_doctor_json_reports_project_state(tmp_path):
    project = tmp_path / "project"
    init_project(project)

    result = CliRunner().invoke(app, ["doctor", str(project), "--json"])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["project"]["is_project"] is True
    names = {item["name"] for item in payload["project"]["checks"]}
    assert "iteris.toml" in names
    assert "tasks/TASK_BOARD.jsonl" in names


def test_doctor_json_non_project_is_not_ready(tmp_path, monkeypatch):
    monkeypatch.setattr(doctor_command.shutil, "which", lambda binary: f"/usr/bin/{binary}")

    result = CliRunner().invoke(app, ["doctor", str(tmp_path), "--json"])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["project"]["is_project"] is False
    assert payload["ok"] is False
    assert payload["ready_for_run"] is False
    assert payload["ready_for_default_run"] is False
    assert payload["ready_for_foreground_run"] is False


def test_doctor_json_codex_hint_mentions_node_when_npm_missing(tmp_path, monkeypatch):
    project = tmp_path / "project"
    init_project(project)

    def fake_which(binary: str) -> str | None:
        if binary == "apt-get":
            return "/usr/bin/apt-get"
        # Both executors missing → no backend → not ready for any run.
        if binary in {"node", "npm", "codex", "claude"}:
            return None
        return f"/usr/bin/{binary}"

    monkeypatch.setattr(doctor_command.platform, "system", lambda: "Linux")
    monkeypatch.setattr(doctor_command.shutil, "which", fake_which)

    result = CliRunner().invoke(app, ["doctor", str(project), "--json"])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["ok"] is True
    assert payload["ready_for_run"] is False
    assert payload["ready_for_foreground_run"] is False
    hints = "\n".join(payload["hints"])
    assert "nodejs npm" in hints
    assert "Install Node/npm first" in hints


def test_doctor_json_tmux_missing_still_allows_foreground_run(tmp_path, monkeypatch):
    project = tmp_path / "project"
    init_project(project)

    def fake_which(binary: str) -> str | None:
        if binary == "tmux":
            return None
        return f"/usr/bin/{binary}"

    monkeypatch.setattr(doctor_command.shutil, "which", fake_which)

    result = CliRunner().invoke(app, ["doctor", str(project), "--json"])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["ok"] is True
    assert payload["ready_for_run"] is False
    assert payload["ready_for_default_run"] is False
    assert payload["ready_for_foreground_run"] is True
    tmux_check = next(item for item in payload["system"]["checks"] if item["name"] == "tmux")
    assert tmux_check["status"] == "warning"


def test_install_script_has_valid_bash_syntax():
    script = Path(__file__).resolve().parents[1] / "install.sh"

    subprocess.run(["bash", "-n", str(script)], check=True)

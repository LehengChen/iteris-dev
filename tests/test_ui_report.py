"""Dashboard report data contracts."""

from __future__ import annotations

import json

from typer.testing import CliRunner

from iteris.cli import app
from iteris.project import init_project

runner = CliRunner()


def _invoke(args: list[str]) -> dict:
    result = runner.invoke(app, args)
    assert result.exit_code == 0, result.output
    return json.loads(result.output)


def test_report_workspaces_and_detail_are_project_relative(tmp_path):
    root = tmp_path / "project"
    init_project(root)
    created = _invoke(
        [
            "report",
            "new",
            str(root),
            "--report-id",
            "demo-report",
            "--title",
            "Demo Report",
            "--evidence",
            "portable",
            "--json",
        ]
    )
    assert created["report"]["report_id"] == "demo-report"

    listing = _invoke(["tool", "ui", "report-workspaces", str(root), "--json"])

    assert listing["schema_version"] == "iteris.ui_report_workspaces.v0"
    assert listing["report_count"] == 1
    assert listing["items"][0]["report_id"] == "demo-report"
    assert listing["items"][0]["main_tex"] == "reports/demo-report/versions/v001/main.tex"

    detail = _invoke(["tool", "ui", "report-workspace", str(root), "--report-id", "demo-report", "--json"])

    assert detail["schema_version"] == "iteris.ui_report_workspace.v0"
    assert detail["report"]["report_id"] == "demo-report"
    assert detail["selected_version"] == "v001"
    assert detail["current"]["main_tex"] == "reports/demo-report/versions/v001/main.tex"
    assert detail["current"]["pdf"] == "reports/demo-report/versions/v001/main.pdf"
    assert detail["evidence"]["schema_version"] == "iteris.report_evidence.v0"
    assert detail["references"]["schema_version"] == "iteris.report_references.v0"
    assert detail["notice"].startswith("portable output")
    assert not _contains_text(detail, str(root))


def test_report_workspace_rejects_invalid_report_id(tmp_path):
    root = tmp_path / "project"
    init_project(root)

    detail = _invoke(["tool", "ui", "report-workspace", str(root), "--report-id", "../secret", "--json"])

    assert detail["schema_version"] == "iteris.ui_report_workspace.v0"
    assert detail["report"] is None


def _contains_text(value, needle: str) -> bool:
    if isinstance(value, dict):
        return any(_contains_text(item, needle) for item in value.values())
    if isinstance(value, list):
        return any(_contains_text(item, needle) for item in value)
    return needle in value if isinstance(value, str) else False

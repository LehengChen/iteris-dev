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
    assert detail["cited_keys"] == []
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


def test_report_workspace_reports_actual_latex_citation_order(tmp_path):
    root = tmp_path / "project"
    init_project(root)
    _invoke(
        [
            "report",
            "new",
            str(root),
            "--report-id",
            "demo-report",
            "--title",
            "Demo Report",
            "--evidence",
            "linked",
            "--json",
        ]
    )
    version = root / "reports" / "demo-report" / "versions" / "v001"
    (version / "main.tex").write_text(
        "\\cite{ref-b, ref-a}\n"
        "% \\cite{commented-out}\n"
        "\\cite[see][p. 3]{ref-b,ref-c}\n"
        "\\parencite{ref-d}\n",
        encoding="utf-8",
    )
    (version / "references.json").write_text(
        json.dumps(
            {
                "schema_version": "iteris.report_references.v0",
                "include_internal": True,
                "entries": [
                    {"key": "ref-a"},
                    {"key": "ref-b"},
                    {"key": "ref-c"},
                    {"key": "ref-d"},
                    {"key": "commented-out"},
                ],
            }
        ),
        encoding="utf-8",
    )

    detail = _invoke(["tool", "ui", "report-workspace", str(root), "--report-id", "demo-report", "--json"])

    assert detail["cited_keys"] == ["ref-b", "ref-a", "ref-c", "ref-d"]


def _contains_text(value, needle: str) -> bool:
    if isinstance(value, dict):
        return any(_contains_text(item, needle) for item in value.values())
    if isinstance(value, list):
        return any(_contains_text(item, needle) for item in value)
    return needle in value if isinstance(value, str) else False

from __future__ import annotations

import json
import zipfile
from pathlib import Path

import pytest
from typer.testing import CliRunner

from iteris.cli import app
from iteris.project import init_project
from iteris.reporting.core import REPORT_SCHEMA
from iteris.reporting.export import export_report


def test_source_export_omits_only_internal_references(tmp_path: Path) -> None:
    root = _export_fixture(tmp_path)
    output = tmp_path / "source-no-internal-refs.zip"

    payload = export_report(
        root,
        report_id="demo-report",
        kind="source-zip",
        include_references=False,
        output=output,
    )

    assert payload["download_name"] == "demo-report-v001-source-no-internal-refs.zip"
    with zipfile.ZipFile(output) as archive:
        names = set(archive.namelist())
        main_tex = archive.read("main.tex").decode("utf-8")
        detail_tex = archive.read("sections/detail.tex").decode("utf-8")
        readme = archive.read("README.md").decode("utf-8")

    assert "references.bib" not in names
    assert "main.bbl" not in names
    assert "literature.bib" in names
    assert "figures/main.pdf" in names
    assert "itfact-demo" not in main_tex
    assert "itver-demo" not in detail_tex
    assert "\\cite{smith2020}" in main_tex
    assert "\\bibliography{literature}" in main_tex
    assert "\\Cite{doe2021}" in detail_tex
    assert "\\autocite*{roe2022}" in detail_tex
    assert "External literature references are preserved" in readme


def test_source_export_rejects_output_inside_version_dir(tmp_path: Path) -> None:
    root = _export_fixture(tmp_path)
    output = root / "reports" / "demo-report" / "versions" / "v001" / "nested.zip"

    with pytest.raises(ValueError, match="inside the report version directory"):
        export_report(root, report_id="demo-report", kind="source-zip", output=output)


def test_report_export_cli_prints_custom_output_path(tmp_path: Path) -> None:
    root = _export_fixture(tmp_path)
    output = tmp_path / "custom-source.zip"

    result = CliRunner().invoke(
        app,
        [
            "report",
            "export",
            str(root),
            "--report-id",
            "demo-report",
            "--kind",
            "source-zip",
            "--output",
            str(output),
        ],
    )

    assert result.exit_code == 0, result.output
    assert str(output) in result.output
    assert output.exists()


def _export_fixture(tmp_path: Path) -> Path:
    root = tmp_path / "project"
    source = tmp_path / "source.tex"
    source.write_text("Problem", encoding="utf-8")
    init_project(root, source=source)
    report_dir = root / "reports" / "demo-report"
    version_dir = report_dir / "versions" / "v001"
    (version_dir / "sections").mkdir(parents=True, exist_ok=True)
    (version_dir / "figures").mkdir(parents=True, exist_ok=True)
    (report_dir / "report.json").write_text(
        json.dumps(
            {
                "schema_version": REPORT_SCHEMA,
                "report_id": "demo-report",
                "title": "Demo Report",
                "template": "iteris-report",
                "style": "theory",
                "evidence_mode": "linked",
                "current_version": "v001",
                "versions": [{"version": "v001", "source_dir": "versions/v001"}],
            }
        ),
        encoding="utf-8",
    )
    (version_dir / "references.json").write_text(
        json.dumps(
            {
                "schema_version": "iteris.report_references.v0",
                "bibliography": "reports/demo-report/versions/v001/references.bib",
                "entries": [
                    {"key": "itevid-demo", "kind": "evidence"},
                    {"key": "itfact-demo", "kind": "fact"},
                    {"key": "itver-demo", "kind": "verification"},
                ],
            }
        ),
        encoding="utf-8",
    )
    (version_dir / "main.tex").write_text(
        "\\documentclass{article}\n"
        "\\begin{document}\n"
        "A mixed citation \\cite{itfact-demo, smith2020} and an internal-only citation \\cite{itevid-demo}.\n"
        "\\begingroup\n"
        "\\sloppy\n"
        "\\bibliographystyle{plain}\n"
        "\\bibliography{references,literature}\n"
        "\\endgroup\n"
        "\\end{document}\n",
        encoding="utf-8",
    )
    (version_dir / "sections" / "detail.tex").write_text(
        "Nested citations \\Cite{itver-demo,doe2021} and \\autocite*{itevid-demo,roe2022}.\n",
        encoding="utf-8",
    )
    (version_dir / "references.bib").write_text("@misc{itfact-demo,title={Internal}}\n", encoding="utf-8")
    (version_dir / "literature.bib").write_text("@article{smith2020,title={External}}\n", encoding="utf-8")
    (version_dir / "figures" / "main.pdf").write_bytes(b"%PDF-1.4\n")
    (version_dir / "main.bbl").write_text("internal build artifact", encoding="utf-8")
    return root

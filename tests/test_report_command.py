from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from typer.testing import CliRunner

from iteris.cli import app
from iteris.guide.lookups import lookups_for_message
from iteris.memory.facts import rebuild_fact_index, write_fact
from iteris.project import init_project
from iteris.reporting.latex import check_latex_environment
from iteris.reporting.latex import _build_commands
from iteris.reporting.references import (
    bibtex_entries_from_evidence_file,
    citation_key_for_artifact,
    citation_key_for_evidence_register,
    citation_key_for_fact,
    citation_key_for_verification,
    render_bibtex,
)


def test_report_status_json_empty_project(tmp_path):
    root = tmp_path / "project"
    init_project(root)

    result = CliRunner().invoke(app, ["report", "status", str(root), "--json"])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["schema_version"] == "iteris.report_status.v0"
    assert payload["reports_dir"] == "reports"
    assert payload["fact_index"] == "memory/facts/FACT_INDEX.jsonl"
    assert payload["report_count"] == 0
    assert "amsart" in payload["templates"]
    assert payload["styles"] == ["theory"]
    assert payload["latex"] is not None


def test_report_new_and_draft_use_relative_evidence_paths(tmp_path):
    root = _report_fixture(tmp_path)

    result = CliRunner().invoke(
        app,
        [
            "report",
            "new",
            str(root),
            "--report-id",
            "demo-report",
            "--title",
            "Demo Report",
            "--json",
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["report"]["report_id"] == "demo-report"
    evidence_path = root / "reports" / "demo-report" / "evidence.json"
    evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
    assert evidence["fact_graph"]["checked_fact_ids"] == ["fact:demo:main"]
    assert evidence["facts"][0]["path"] == "memory/facts/fact-demo-main.md"
    assert not _contains_text(evidence, str(root))

    main_tex = root / "reports" / "demo-report" / "versions" / "v001" / "main.tex"
    text = main_tex.read_text(encoding="utf-8")
    references_bib = root / "reports" / "demo-report" / "versions" / "v001" / "references.bib"
    references_json = root / "reports" / "demo-report" / "references.json"
    assert "\\documentclass{amsart}" in text
    assert "\\cite{" in text
    assert "\\bibliographystyle{amsplain}" in text
    assert "\\bibliography{references}" in text
    assert "Evidence Register" in text
    assert "\\iterisref{" not in text
    assert "evidence.json" in text
    assert references_bib.exists()
    assert references_json.exists()
    assert "memory/facts/fact-demo-main.md" in references_bib.read_text(encoding="utf-8")
    assert not _contains_text(json.loads(references_json.read_text(encoding="utf-8")), str(root))
    assert str(root) not in references_bib.read_text(encoding="utf-8")


def test_report_portable_mode_hides_evidence_appendix(tmp_path):
    root = _report_fixture(tmp_path)
    create = CliRunner().invoke(app, ["report", "new", str(root), "--report-id", "demo", "--json"])
    assert create.exit_code == 0, create.output

    config = CliRunner().invoke(app, ["report", "config", str(root), "--report-id", "demo", "--evidence", "portable", "--json"])
    assert config.exit_code == 0, config.output
    draft = CliRunner().invoke(app, ["report", "draft", str(root), "--report-id", "demo", "--json"])
    assert draft.exit_code == 0, draft.output

    main_tex = root / "reports" / "demo" / "versions" / "v001" / "main.tex"
    text = main_tex.read_text(encoding="utf-8")
    assert "Evidence Register" not in text
    assert "\\iterisref{" not in text
    assert "\\cite{" not in text
    assert "\\bibliography{references}" not in text
    assert not (root / "reports" / "demo" / "versions" / "v001" / "references.bib").exists()
    assert (root / "reports" / "demo" / "evidence.json").exists()


def test_report_evidence_generates_bibtex_references(tmp_path):
    root = _report_fixture(tmp_path)
    result = CliRunner().invoke(app, ["report", "new", str(root), "--report-id", "demo-report", "--json"])
    assert result.exit_code == 0, result.output

    entries = bibtex_entries_from_evidence_file(root / "reports" / "demo-report" / "evidence.json")
    by_key = {entry.citation_key: entry for entry in entries}

    expected_keys = {
        citation_key_for_evidence_register("demo-report"),
        citation_key_for_fact("fact:demo:main"),
        citation_key_for_verification("verify-goal"),
        citation_key_for_verification("verify-assembly"),
        citation_key_for_verification("verify-fact"),
        citation_key_for_artifact("results/demo/answer.md"),
    }
    assert expected_keys <= set(by_key)
    for key in expected_keys:
        entry = by_key[key]
        assert entry.entry_type == "misc"
        assert {"title", "howpublished", "note", "year"} <= set(entry.fields)

    fact_entry = by_key[citation_key_for_fact("fact:demo:main")]
    assert fact_entry.fields["howpublished"] == "Project file: memory/facts/fact-demo-main.md"
    evidence_entry = by_key[citation_key_for_evidence_register("demo-report")]
    assert evidence_entry.fields["howpublished"] == "Project file: reports/demo-report/evidence.json"

    rendered = render_bibtex(entries)
    assert f"@misc{{{citation_key_for_fact('fact:demo:main')}," in rendered
    assert r"Project file: \path{reports/demo-report/evidence.json}" in rendered
    assert str(root) not in rendered


def test_report_build_commands_run_bibtex_when_references_exist(tmp_path):
    commands = _build_commands("xelatex", "main.tex", tmp_path / "build", needs_bibtex=True)

    assert [command[0] for command in commands] == ["xelatex", "bibtex", "xelatex", "xelatex"]


def test_report_lookup_triggered_by_latex_keywords(tmp_path):
    root = _report_fixture(tmp_path)
    lookups = lookups_for_message(root, "write a LaTeX report")

    assert "report_status" in lookups
    assert lookups["report_status"]["reports_dir"] == "reports"
    assert lookups["report_status"]["fact_index"] == "memory/facts/FACT_INDEX.jsonl"
    assert "latex" not in lookups["report_status"]


def test_report_missing_project_explains_new(tmp_path):
    result = CliRunner().invoke(app, ["report", "status", str(tmp_path), "--json"])

    assert result.exit_code != 0
    assert "not an Iteris project" in result.output
    assert "new" in result.output
    assert "--source" in result.output
    assert "Traceback" not in result.output


def test_report_latex_environment_contract(monkeypatch):
    monkeypatch.setattr("iteris.reporting.latex.shutil.which", lambda _binary: None)

    payload = check_latex_environment()

    assert payload["has_engine"] is False
    assert payload["preferred_engine"] == ""
    assert "Install" in payload["install_hint"]


def _report_fixture(tmp_path: Path) -> Path:
    source = tmp_path / "source.tex"
    source.write_text("Problem", encoding="utf-8")
    root = tmp_path / "project"
    init_project(root, source=source)
    target = "results/demo/answer.md"
    (root / "STATUS.md").write_text(
        "phase: goal_success_verified\n"
        f"target_artifact: {target}\n"
        "terminal_assembly_verification: verify-assembly\n"
        "terminal_goal_success_verification: verify-goal\n"
        "verified_positive_result: Demo theorem holds.\n"
        "verified_facts:\n"
        "  - fact:demo:main\n",
        encoding="utf-8",
    )
    (root / target).parent.mkdir(parents=True, exist_ok=True)
    (root / target).write_text(
        "# Demo Answer\n\n"
        "## Assembly\n\n"
        "The verified fact proves the claim.\n",
        encoding="utf-8",
    )
    write_fact(
        root,
        fact_id="fact:demo:main",
        source_task="task-demo",
        claim_summary="Demo theorem holds.",
        statement="The demo theorem holds.",
        status="verified",
        verification="verify-fact",
        review_level="agent",
    )
    rebuild_fact_index(root)
    (root / "verification" / "results" / "verify-goal.json").write_text(
        json.dumps(
            {
                "schema_version": "iteris.verification_result.v0",
                "request_id": "verify-goal",
                "mode": "goal_success",
                "passed": True,
                "summary": "Goal passed.",
                "checked_fact_ids": ["fact:demo:main"],
                "checked_artifacts": [target, "memory/facts/fact-demo-main.md"],
                "target_artifact": target,
                "created_at": "2026-06-18T00:00:00Z",
            }
        ),
        encoding="utf-8",
    )
    (root / "verification" / "results" / "verify-assembly.json").write_text(
        json.dumps(
            {
                "schema_version": "iteris.verification_result.v0",
                "request_id": "verify-assembly",
                "mode": "assembly",
                "passed": True,
                "summary": "Assembly passed.",
                "checked_fact_ids": ["fact:demo:main"],
                "checked_artifacts": [target],
                "target_artifact": target,
                "created_at": "2026-06-18T00:00:00Z",
            }
        ),
        encoding="utf-8",
    )
    return root


def _contains_text(value: Any, needle: str) -> bool:
    if isinstance(value, dict):
        return any(_contains_text(item, needle) for item in value.values())
    if isinstance(value, list):
        return any(_contains_text(item, needle) for item in value)
    return needle in value if isinstance(value, str) else False

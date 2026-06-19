from __future__ import annotations

import json
import zipfile
from pathlib import Path
from typing import Any

from typer.testing import CliRunner

from iteris.cli import app
from iteris.guide.lookups import lookups_for_message
from iteris.memory.facts import rebuild_fact_index, write_fact
from iteris.project import init_project
from iteris.reporting.latex import check_latex_environment
from iteris.reporting.latex import _build_commands
from iteris.reporting.latex import _source_preferred_engine
from iteris.reporting.references import (
    bibtex_entries_from_evidence_file,
    citation_key_for_artifact,
    citation_key_for_evidence_register,
    citation_key_for_fact,
    citation_key_for_verification,
    render_bibtex,
)


REPORT_FORBIDDEN_TERMS = (
    "si" "am",
    "ams" "art",
    "publish" "er",
    "journal" "-style",
    "期" "刊",
    "出版" "商",
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
    assert payload["templates"] == ["iteris-report"]
    assert payload["styles"] == ["theory"]
    assert payload["latex"] is not None
    _assert_no_report_template_forbidden_terms(json.dumps(payload, ensure_ascii=False))


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
    references_json = root / "reports" / "demo-report" / "versions" / "v001" / "references.json"
    version_evidence = root / "reports" / "demo-report" / "versions" / "v001" / "evidence.json"
    assert "\\documentclass[11pt]{article}" in text
    assert "\\usepackage{iterisreport}" in text
    assert "\\author{\\reportauthors}" in text
    assert "\\cite{" in text
    assert "\\bibliographystyle{plain}" in text
    assert "\\bibliography{references}" in text
    assert "Evidence Register" in text
    assert "\\iterisref{" not in text
    assert "evidence.json" in text
    assert references_bib.exists()
    assert references_json.exists()
    assert version_evidence.exists()
    assert (root / "reports" / "demo-report" / "versions" / "v001" / "iterisreport.sty").exists()
    assert "memory/facts/fact-demo-main.md" in references_bib.read_text(encoding="utf-8")
    assert not _contains_text(json.loads(references_json.read_text(encoding="utf-8")), str(root))
    assert str(root) not in references_bib.read_text(encoding="utf-8")
    assert "Iteris" not in text.replace("\\author{\\reportauthors}", "")
    _assert_no_report_template_forbidden_terms(text)


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
    assert (root / "reports" / "demo" / "versions" / "v001" / "evidence.json").exists()
    references = json.loads((root / "reports" / "demo" / "versions" / "v001" / "references.json").read_text(encoding="utf-8"))
    version_evidence = json.loads((root / "reports" / "demo" / "versions" / "v001" / "evidence.json").read_text(encoding="utf-8"))
    assert references["bibliography"] == ""
    assert references["include_internal"] is False
    assert references["entries"] == []
    assert references["omitted_reason"] == "portable evidence mode omits internal project citations"
    assert version_evidence["citations"]["bibliography"] == ""
    assert version_evidence["citations"]["omitted_reason"] == references["omitted_reason"]


def test_report_evidence_rejects_paths_outside_project(tmp_path):
    root = _report_fixture(tmp_path)
    outside = tmp_path / "outside.md"
    outside.write_text("outside sentinel must not appear", encoding="utf-8")
    (root / "STATUS.md").write_text(
        "phase: goal_success_verified\n"
        "target_artifact: ../outside.md\n"
        "terminal_assembly_verification: verify-assembly\n"
        "terminal_goal_success_verification: verify-goal\n"
        "verified_positive_result: Demo theorem holds.\n",
        encoding="utf-8",
    )

    result = CliRunner().invoke(app, ["report", "new", str(root), "--report-id", "confined", "--json"])

    assert result.exit_code == 0, result.output
    evidence = json.loads((root / "reports" / "confined" / "versions" / "v001" / "evidence.json").read_text(encoding="utf-8"))
    text = (root / "reports" / "confined" / "versions" / "v001" / "main.tex").read_text(encoding="utf-8")
    assert evidence["answer"]["target_artifact"] == ""
    assert all(record["role"] != "target_artifact" for record in evidence["source_paths"])
    assert "outside sentinel" not in text


def test_report_evidence_does_not_fallback_to_unchecked_facts(tmp_path):
    root = _report_fixture(tmp_path)
    (root / "STATUS.md").write_text(
        "phase: goal_success_verified\n"
        "target_artifact: results/demo/answer.md\n"
        "terminal_assembly_verification: verify-assembly\n"
        "terminal_goal_success_verification: verify-goal\n"
        "verified_positive_result: Demo theorem holds.\n",
        encoding="utf-8",
    )
    for name in ["verify-goal", "verify-assembly"]:
        path = root / "verification" / "results" / f"{name}.json"
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload["checked_fact_ids"] = []
        path.write_text(json.dumps(payload), encoding="utf-8")

    result = CliRunner().invoke(app, ["report", "new", str(root), "--report-id", "unchecked", "--json"])

    assert result.exit_code == 0, result.output
    evidence = json.loads((root / "reports" / "unchecked" / "versions" / "v001" / "evidence.json").read_text(encoding="utf-8"))
    assert evidence["fact_graph"]["checked_fact_ids"] == []
    assert evidence["facts"] == []


def test_report_evidence_generates_bibtex_references(tmp_path):
    root = _report_fixture(tmp_path)
    result = CliRunner().invoke(app, ["report", "new", str(root), "--report-id", "demo-report", "--json"])
    assert result.exit_code == 0, result.output

    entries = bibtex_entries_from_evidence_file(root / "reports" / "demo-report" / "evidence.json")
    by_key = {entry.citation_key: entry for entry in entries}

    expected_keys = {
        citation_key_for_evidence_register("demo-report", version="v001"),
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
    evidence_entry = by_key[citation_key_for_evidence_register("demo-report", version="v001")]
    assert evidence_entry.fields["howpublished"] == "Project file: reports/demo-report/versions/v001/evidence.json"

    rendered = render_bibtex(entries)
    assert f"@misc{{{citation_key_for_fact('fact:demo:main')}," in rendered
    assert r"Project file: \path{reports/demo-report/versions/v001/evidence.json}" in rendered
    assert "https://example.com/check" not in rendered
    assert str(root) not in rendered


def test_report_build_commands_run_bibtex_when_references_exist(tmp_path):
    commands = _build_commands("xelatex", "main.tex", tmp_path / "build", needs_bibtex=True)

    assert [command[0] for command in commands] == ["xelatex", "bibtex", "xelatex", "xelatex"]


def test_report_auto_engine_uses_environment_preference(tmp_path):
    main_tex = tmp_path / "main.tex"
    main_tex.write_text("\\documentclass[11pt]{article}\\begin{document}x\\end{document}", encoding="utf-8")

    engine = _source_preferred_engine(main_tex, {"preferred_engine": "xelatex", "engines": {"pdflatex": "/usr/bin/pdflatex"}})

    assert engine == "xelatex"


def test_report_draft_new_version_keeps_generic_layout(tmp_path):
    root = _report_fixture(tmp_path)
    create = CliRunner().invoke(app, ["report", "new", str(root), "--report-id", "demo-report", "--json"])
    assert create.exit_code == 0, create.output

    draft = CliRunner().invoke(
        app,
        ["report", "draft", str(root), "--report-id", "demo-report", "--new-version", "--json"],
    )

    assert draft.exit_code == 0, draft.output
    payload = json.loads(draft.output)
    assert payload["version"] == "v002"
    assert payload["template"] == "iteris-report"
    main_tex = root / "reports" / "demo-report" / "versions" / "v002" / "main.tex"
    text = main_tex.read_text(encoding="utf-8")
    assert "\\documentclass[11pt]{article}" in text
    assert "\\usepackage{iterisreport}" in text
    assert "\\bibliographystyle{plain}" in text
    report = json.loads((root / "reports" / "demo-report" / "report.json").read_text(encoding="utf-8"))
    assert report["versions"][0]["template"] == "iteris-report"
    assert report["versions"][1]["template"] == "iteris-report"
    assert (root / "reports" / "demo-report" / "versions" / "v002" / "template.assets.json").exists()
    assert (root / "reports" / "demo-report" / "versions" / "v002" / "references.json").exists()
    assert (root / "reports" / "demo-report" / "versions" / "v002" / "evidence.json").exists()


def test_report_export_source_zip_is_overleaf_ready_and_omits_audit_json(tmp_path):
    root = _report_fixture(tmp_path)
    create = CliRunner().invoke(app, ["report", "new", str(root), "--report-id", "demo-report", "--json"])
    assert create.exit_code == 0, create.output
    output = tmp_path / "demo-source.zip"

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
            "--json",
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["kind"] == "source-zip"
    assert payload["download_name"] == "demo-report-v001-source.zip"
    assert output.exists()
    with zipfile.ZipFile(output) as archive:
        names = set(archive.namelist())
        assert {"main.tex", "references.bib", "iterisreport.sty", "README.md", "EXPORT_MANIFEST.json"} <= names
        assert "evidence.json" not in names
        assert "references.json" not in names
        assert "template.lock.json" not in names
        manifest = json.loads(archive.read("EXPORT_MANIFEST.json").decode("utf-8"))
    assert manifest["entrypoint"] == "main.tex"
    assert manifest["files"] == sorted(["iterisreport.sty", "main.tex", "references.bib"])


def test_report_export_source_zip_can_omit_references(tmp_path):
    root = _report_fixture(tmp_path)
    create = CliRunner().invoke(app, ["report", "new", str(root), "--report-id", "demo-report", "--json"])
    assert create.exit_code == 0, create.output
    output = tmp_path / "demo-source-no-refs.zip"

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
            "--no-references",
            "--output",
            str(output),
            "--json",
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["download_name"] == "demo-report-v001-source-no-refs.zip"
    assert payload["references"] == "omitted"
    with zipfile.ZipFile(output) as archive:
        names = set(archive.namelist())
        main_tex = archive.read("main.tex").decode("utf-8")
        manifest = json.loads(archive.read("EXPORT_MANIFEST.json").decode("utf-8"))
        readme = archive.read("README.md").decode("utf-8")
    assert "references.bib" not in names
    assert "\\cite{" not in main_tex
    assert "\\bibliography{references}" not in main_tex
    assert "Evidence Register" not in main_tex
    assert manifest["references"] == "omitted"
    assert "omits the report bibliography" in readme


def test_report_export_pdf_requires_built_pdf(tmp_path):
    root = _report_fixture(tmp_path)
    create = CliRunner().invoke(app, ["report", "new", str(root), "--report-id", "demo-report", "--json"])
    assert create.exit_code == 0, create.output
    pdf = root / "reports" / "demo-report" / "versions" / "v001" / "main.pdf"
    pdf.write_bytes(b"%PDF-1.4\n")
    output = tmp_path / "demo.pdf"

    result = CliRunner().invoke(
        app,
        [
            "report",
            "export",
            str(root),
            "--report-id",
            "demo-report",
            "--kind",
            "pdf",
            "--output",
            str(output),
            "--json",
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["kind"] == "pdf"
    assert payload["download_name"] == "demo-report-v001-report.pdf"
    assert output.read_bytes() == b"%PDF-1.4\n"


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
                "checked_artifacts": [target, "memory/facts/fact-demo-main.md", "https://example.com/check"],
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


def _assert_no_report_template_forbidden_terms(text: str) -> None:
    lowered = text.lower()
    for term in REPORT_FORBIDDEN_TERMS:
        assert term not in lowered

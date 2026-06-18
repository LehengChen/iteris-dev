"""Report drafting and LaTeX export commands."""

from __future__ import annotations

import json

import typer

from iteris import log
from iteris.commands.common import require_public_project
from iteris.reporting import add_feedback, build_report, configure_report, create_report, draft_report, report_status
from iteris.reporting.latex import check_latex_environment
from iteris.reporting.templates import DEFAULT_STYLE_ID, DEFAULT_TEMPLATE_ID

app = typer.Typer(help="Draft, version, and build research reports from verified Iteris projects.")
feedback_app = typer.Typer(help="Record human feedback for a report.")


@app.command("status")
def status(
    project_path: str = typer.Argument(".", help="Iteris project path."),
    json_output: bool = typer.Option(False, "--json", help="Print machine-readable JSON."),
) -> None:
    """Show report inventory, LaTeX environment, and next report actions."""
    root = require_public_project(project_path)
    payload = report_status(root, include_latex=True)
    if json_output:
        typer.echo(json.dumps(payload, indent=2, ensure_ascii=False))
        return
    log.key_value(
        {
            "Reports": str(payload["report_count"]),
            "Reports dir": payload["reports_dir"],
            "Layouts": ", ".join(payload.get("templates") or []),
            "LaTeX engine": (payload.get("latex") or {}).get("preferred_engine") or "(missing)",
            "Standard LaTeX": "available" if (payload.get("latex") or {}).get("standard_layout_available") else "not found",
        }
    )
    rows = [
        (
            item["report_id"],
            item.get("evidence_mode") or "",
            item.get("main_tex") or "",
        )
        for item in payload["reports"]
    ]
    log.results_table(rows or [("no reports", "ok", "create one with `iteris report new`")], title="Reports")


@app.command("new")
def new(
    project_path: str = typer.Argument(".", help="Iteris project path."),
    report_id: str | None = typer.Option(None, "--report-id", help="Stable report id under reports/."),
    title: str | None = typer.Option(None, "--title", help="Report title."),
    template: str = typer.Option(DEFAULT_TEMPLATE_ID, "--layout", "--template", help="Report layout. Available: iteris-report."),
    style: str = typer.Option(DEFAULT_STYLE_ID, "--profile", "--style", help="Writing profile. Available: theory."),
    evidence: str = typer.Option("linked", "--evidence", help="Evidence mode: linked or portable."),
    no_draft: bool = typer.Option(False, "--no-draft", help="Create metadata only; do not render main.tex."),
    json_output: bool = typer.Option(False, "--json", help="Print machine-readable JSON."),
) -> None:
    """Create a versioned report workspace and initial draft."""
    root = require_public_project(project_path)
    try:
        payload = create_report(
            root,
            report_id=report_id,
            title=title,
            template=template,
            style=style,
            evidence=evidence,
            draft=not no_draft,
        )
    except (FileExistsError, ValueError) as exc:
        raise typer.BadParameter(str(exc)) from exc
    if json_output:
        typer.echo(json.dumps(payload, indent=2, ensure_ascii=False))
        return
    report = payload["report"]
    log.success(f"Report ready: reports/{report['report_id']}")
    if payload.get("draft"):
        log.info(f"Draft: {payload['draft']['main_tex']}")


@app.command("draft")
def draft(
    project_path: str = typer.Argument(".", help="Iteris project path."),
    report_id: str = typer.Option(..., "--report-id", help="Report id under reports/."),
    new_version: bool = typer.Option(False, "--new-version", help="Create a new source version instead of rewriting the current one."),
    evidence: str | None = typer.Option(None, "--evidence", help="Override evidence mode for this draft: linked or portable."),
    template: str | None = typer.Option(None, "--layout", "--template", help="Override report layout for this draft/version."),
    style: str | None = typer.Option(None, "--profile", "--style", help="Override writing profile for this draft/version."),
    json_output: bool = typer.Option(False, "--json", help="Print machine-readable JSON."),
) -> None:
    """Render or re-render the report LaTeX source."""
    root = require_public_project(project_path)
    try:
        payload = draft_report(root, report_id=report_id, new_version=new_version, evidence=evidence, template=template, style=style)
    except (FileNotFoundError, ValueError) as exc:
        raise typer.BadParameter(str(exc)) from exc
    if json_output:
        typer.echo(json.dumps(payload, indent=2, ensure_ascii=False))
        return
    log.success(f"Drafted {payload['main_tex']}")


@app.command("build")
def build(
    project_path: str = typer.Argument(".", help="Iteris project path."),
    report_id: str = typer.Option(..., "--report-id", help="Report id under reports/."),
    engine: str = typer.Option("auto", "--engine", help="LaTeX engine: auto, latexmk, tectonic, xelatex, pdflatex."),
    json_output: bool = typer.Option(False, "--json", help="Print machine-readable JSON."),
) -> None:
    """Build the current report version into a PDF."""
    root = require_public_project(project_path)
    try:
        payload = build_report(root, report_id=report_id, engine=engine)
    except FileNotFoundError as exc:
        raise typer.BadParameter(str(exc)) from exc
    if json_output:
        typer.echo(json.dumps(payload, indent=2, ensure_ascii=False))
        return
    if payload.get("ok"):
        log.success(f"Built {payload['pdf']}")
    else:
        log.warn(str(payload.get("error") or "LaTeX build failed"))
        hint = payload.get("environment", {}).get("install_hint")
        if hint:
            log.info(str(hint))


@app.command("doctor")
def doctor(
    project_path: str = typer.Argument(".", help="Iteris project path."),
    json_output: bool = typer.Option(False, "--json", help="Print machine-readable JSON."),
) -> None:
    """Check report-specific LaTeX/template availability."""
    require_public_project(project_path)
    payload = check_latex_environment()
    if json_output:
        typer.echo(json.dumps(payload, indent=2, ensure_ascii=False))
        return
    log.key_value(
        {
            "Engine": payload.get("preferred_engine") or "(missing)",
            "article.cls": payload.get("article_cls") or "(not found)",
            "plain.bst": payload.get("plain_bst") or "(not found)",
            "BibTeX": "available" if payload.get("bibtex_available") else "(missing)",
            "Install hint": payload.get("install_hint") or "",
        }
    )


@app.command("config")
def config(
    project_path: str = typer.Argument(".", help="Iteris project path."),
    report_id: str = typer.Option(..., "--report-id", help="Report id under reports/."),
    evidence: str = typer.Option(..., "--evidence", help="Evidence mode: linked or portable."),
    json_output: bool = typer.Option(False, "--json", help="Print machine-readable JSON."),
) -> None:
    """Update report configuration without deleting evidence files."""
    root = require_public_project(project_path)
    try:
        payload = configure_report(root, report_id=report_id, evidence=evidence)
    except (FileNotFoundError, ValueError) as exc:
        raise typer.BadParameter(str(exc)) from exc
    if json_output:
        typer.echo(json.dumps(payload, indent=2, ensure_ascii=False))
        return
    log.success(f"Report {payload['report_id']} evidence mode: {payload['evidence_mode']}")


@feedback_app.command("add")
def feedback_add(
    project_path: str = typer.Argument(".", help="Iteris project path."),
    report_id: str = typer.Option(..., "--report-id", help="Report id under reports/."),
    section: str = typer.Option("general", "--section", help="Section id or heading."),
    text: str = typer.Option(..., "--text", help="Human feedback text."),
    json_output: bool = typer.Option(False, "--json", help="Print machine-readable JSON."),
) -> None:
    """Append human feedback for a future report revision."""
    root = require_public_project(project_path)
    try:
        payload = add_feedback(root, report_id=report_id, section=section, text=text)
    except FileNotFoundError as exc:
        raise typer.BadParameter(str(exc)) from exc
    if json_output:
        typer.echo(json.dumps(payload, indent=2, ensure_ascii=False))
        return
    log.success(f"Recorded feedback in reports/{payload['report_id']}/{payload['feedback']}")


app.add_typer(feedback_app, name="feedback")

"""Dashboard data contract for versioned research reports."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import typer

from iteris.project import read_json, require_project, slugify
from iteris.reporting.core import report_status


def register(app: typer.Typer) -> None:
    app.command()(report_workspaces)
    app.command()(report_workspace)


def report_workspaces(
    project_path: str = typer.Argument(".", help="Iteris project path."),
    json_output: bool = typer.Option(False, "--json", help="Print machine-readable JSON."),
) -> None:
    """Versioned research reports under reports/, newest first."""
    root = require_project(project_path)
    status = report_status(root, include_latex=False)
    payload = {
        "schema_version": "iteris.ui_report_workspaces.v0",
        "reports_dir": status.get("reports_dir"),
        "report_index": status.get("report_index"),
        "fact_index": status.get("fact_index"),
        "templates": status.get("templates", []),
        "styles": status.get("styles", []),
        "report_count": status.get("report_count", 0),
        "items": [_workspace_item(root, item) for item in status.get("reports", [])],
    }
    typer.echo(json.dumps(payload, indent=2 if json_output else None, ensure_ascii=False))


def report_workspace(
    project_path: str = typer.Argument(".", help="Iteris project path."),
    report_id: str = typer.Option(..., "--report-id", help="Report id under reports/."),
    version: str = typer.Option("", "--version", help="Report version, defaults to current."),
    json_output: bool = typer.Option(False, "--json", help="Print machine-readable JSON."),
) -> None:
    """One report workspace: versions, current evidence, references, and paths."""
    root = require_project(project_path)
    report_id = _valid_report_id(report_id)
    if not report_id:
        typer.echo(json.dumps({"schema_version": "iteris.ui_report_workspace.v0", "report": None}))
        return

    report_dir = root / "reports" / report_id
    report = read_json(report_dir / "report.json", default={})
    if not isinstance(report, dict):
        typer.echo(json.dumps({"schema_version": "iteris.ui_report_workspace.v0", "report": None}))
        return

    versions = [_version_summary(root, report_id, row) for row in _version_rows(report)]
    selected = _select_version(report, versions, version)
    version_dir = report_dir / "versions" / selected if selected else None
    evidence = _read_version_json(version_dir, "evidence.json") if version_dir else None
    references = _read_version_json(version_dir, "references.json") if version_dir else None
    template_lock = _read_version_json(version_dir, "template.lock.json") if version_dir else None
    current = next((item for item in versions if item.get("version") == selected), None)

    payload = {
        "schema_version": "iteris.ui_report_workspace.v0",
        "report": _report_meta(report),
        "versions": versions,
        "selected_version": selected,
        "current": current,
        "evidence": evidence,
        "references": references,
        "template_lock": template_lock,
        "author_draft": _file_record(root, f"reports/{report_id}/author_draft.md"),
        "feedback": _file_record(root, f"reports/{report_id}/feedback.md"),
        "revision_log": _file_record(root, f"reports/{report_id}/REVISION_LOG.md"),
        "notice": _portable_notice(report),
    }
    typer.echo(json.dumps(payload, indent=2 if json_output else None, ensure_ascii=False))


def _workspace_item(root: Path, item: dict[str, Any]) -> dict[str, Any]:
    report_id = str(item.get("report_id") or "")
    current = str(item.get("current_version") or "")
    report = read_json(root / "reports" / report_id / "report.json", default={})
    return {
        **item,
        "version_count": len(_version_rows(report)),
        "pdf": (
            f"reports/{report_id}/versions/{current}/main.pdf"
            if report_id and current and item.get("pdf_exists")
            else ""
        ),
    }


def _report_meta(report: dict[str, Any]) -> dict[str, Any]:
    keys = [
        "schema_version",
        "report_id",
        "title",
        "template",
        "style",
        "style_profile",
        "evidence_mode",
        "created_at",
        "updated_at",
        "current_version",
        "paths",
    ]
    return {key: report.get(key) for key in keys if key in report}


def _version_rows(report: dict[str, Any]) -> list[dict[str, Any]]:
    rows = report.get("versions") if isinstance(report.get("versions"), list) else []
    return [row for row in rows if isinstance(row, dict)]


def _version_summary(root: Path, report_id: str, row: dict[str, Any]) -> dict[str, Any]:
    version = str(row.get("version") or "")
    main_tex = _report_rel(report_id, str(row.get("main_tex") or f"versions/{version}/main.tex"))
    pdf = _pdf_path(main_tex)
    evidence = _report_rel(report_id, str(row.get("evidence") or f"versions/{version}/evidence.json"))
    references = _report_rel(report_id, str(row.get("references") or f"versions/{version}/references.json"))
    out = {
        "version": version,
        "created_at": row.get("created_at"),
        "template": row.get("template"),
        "style": row.get("style"),
        "main_tex": main_tex,
        "main_tex_exists": _exists(root, main_tex),
        "pdf": pdf,
        "pdf_exists": _exists(root, pdf),
        "evidence": evidence,
        "evidence_exists": _exists(root, evidence),
        "references": references,
        "references_exists": _exists(root, references),
        "template_lock": _report_rel(
            report_id,
            str(row.get("template_lock") or f"versions/{version}/template.lock.json"),
        ),
        "template_assets": _report_rel(
            report_id,
            str(row.get("template_assets") or f"versions/{version}/template.assets.json"),
        ),
    }
    if row.get("references_bib"):
        out["references_bib"] = _report_rel(report_id, str(row["references_bib"]))
    return out


def _select_version(report: dict[str, Any], versions: list[dict[str, Any]], requested: str) -> str:
    available = {str(item.get("version")) for item in versions if item.get("version")}
    if requested and requested in available:
        return requested
    current = str(report.get("current_version") or "")
    if current in available:
        return current
    return str(versions[-1].get("version") or "") if versions else ""


def _read_version_json(version_dir: Path | None, filename: str) -> Any:
    if version_dir is None:
        return None
    value = read_json(version_dir / filename, default=None)
    return value if isinstance(value, dict) else None


def _file_record(root: Path, rel: str) -> dict[str, Any]:
    rel = rel.replace("\\", "/")
    path = root / rel
    return {"path": rel, "exists": path.is_file()}


def _exists(root: Path, rel: str) -> bool:
    return bool(rel and (root / rel).is_file())


def _report_rel(report_id: str, rel: str) -> str:
    rel = rel.replace("\\", "/").lstrip("/")
    if rel.startswith(f"reports/{report_id}/"):
        return rel
    return f"reports/{report_id}/{rel}"


def _pdf_path(main_tex: str) -> str:
    if main_tex.endswith(".tex"):
        return main_tex[:-4] + ".pdf"
    return ""


def _valid_report_id(value: str) -> str:
    value = str(value or "").strip()
    if not value or value != slugify(value, 64):
        return ""
    return value


def _portable_notice(report: dict[str, Any]) -> str:
    if report.get("evidence_mode") == "portable":
        return "portable output omits internal citations; dashboard evidence remains visible"
    return ""

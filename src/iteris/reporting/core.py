"""Report lifecycle: status, versions, feedback, and builds."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from iteris.gitops import ensure_gitignore
from iteris.project import append_jsonl, now_iso, now_stamp, read_json, slugify, write_json
from iteris.reporting.evidence import collect_evidence
from iteris.reporting.latex import build_latex, check_latex_environment
from iteris.reporting.references import build_reference_registry, render_bibtex
from iteris.reporting.render import render_report
from iteris.reporting.templates import style_profile, template_manifest
from iteris.reporting.utils import parse_status, read_project_text

REPORT_SCHEMA = "iteris.report.v0"
REPORT_INDEX_SCHEMA = "iteris.report_index_line.v0"
REPORT_STATUS_SCHEMA = "iteris.report_status.v0"
SUPPORTED_EVIDENCE_MODES = {"linked", "portable"}


def report_status(project_root: Path, *, include_latex: bool = False) -> dict[str, Any]:
    root = project_root.resolve()
    reports = [_compact_report(root, item) for item in list_reports(root)]
    return {
        "schema_version": REPORT_STATUS_SCHEMA,
        "reports_dir": "reports",
        "reports_exists": (root / "reports").is_dir(),
        "report_index": "reports/REPORT_INDEX.jsonl",
        "third_party_tex_cache": "third_party_tex",
        "stage_reports_dir": "artifacts/reports",
        "fact_index": "memory/facts/FACT_INDEX.jsonl",
        "reports": reports,
        "report_count": len(reports),
        "recent_reports": reports[:5],
        "latex": check_latex_environment() if include_latex else None,
        "templates": ["amsart"],
        "styles": ["theory"],
        "cli": {
            "status": "iteris report status . --json",
            "new": "iteris report new . --report-id <id> --template amsart --style theory",
            "draft": "iteris report draft . --report-id <id>",
            "doctor": "iteris report doctor . --json",
            "build": "iteris report build . --report-id <id>",
            "portable": "iteris report config . --report-id <id> --evidence portable",
        },
        "switches": {
            "evidence": "--evidence linked|portable",
            "versioning": "--new-version on draft creates a new immutable source version",
            "build_deps": "use `iteris report doctor` for TeX install hints",
        },
    }


def list_reports(project_root: Path) -> list[dict[str, Any]]:
    root = project_root.resolve()
    reports_dir = root / "reports"
    rows: list[dict[str, Any]] = []
    if not reports_dir.exists():
        return rows
    for path in sorted(reports_dir.glob("*/report.json")):
        data = read_json(path, default={})
        if isinstance(data, dict):
            rows.append(data)
    rows.sort(key=lambda item: str(item.get("updated_at") or item.get("created_at") or ""), reverse=True)
    return rows


def create_report(
    project_root: Path,
    *,
    report_id: str | None = None,
    template: str = "amsart",
    style: str = "theory",
    title: str | None = None,
    evidence: str = "linked",
    draft: bool = True,
) -> dict[str, Any]:
    root = project_root.resolve()
    manifest = template_manifest(template)
    profile = style_profile(style)
    evidence_mode = _validate_evidence_mode(evidence)
    reports_dir = root / "reports"
    reports_dir.mkdir(parents=True, exist_ok=True)
    ensure_gitignore(root)

    resolved_id = _report_id(report_id or _default_report_id(root, title))
    directory = _report_dir(root, resolved_id)
    if (directory / "report.json").exists():
        raise FileExistsError(f"report already exists: reports/{resolved_id}")
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "versions" / "v001" / "sections").mkdir(parents=True, exist_ok=True)
    now = now_iso()
    report = _new_report_payload(
        report_id=resolved_id,
        title=title or _default_title(root),
        template=template,
        style=style,
        profile=profile,
        evidence_mode=evidence_mode,
        now=now,
    )
    write_json(directory / "report.json", report)
    lock = dict(manifest)
    lock["locked_at"] = now
    lock["lock_reason"] = "MVP uses local TeX distribution files; no upstream class/style files are vendored."
    write_json(directory / "template.lock.json", lock)
    write_json(directory / "evidence.json", collect_evidence(root, report_id=resolved_id))
    _write_if_missing(directory / "feedback.md", f"# Feedback for {resolved_id}\n\n")
    _write_if_missing(directory / "feedback.jsonl", "")
    _write_if_missing(directory / "REVISION_LOG.md", f"# Revision Log\n\n- {now}: created report `{resolved_id}`.\n")
    _write_writing_plan(directory, report)
    _write_report_index(root)
    payload = {"report": _compact_report(root, report), "created": True}
    if draft:
        payload["draft"] = draft_report(root, report_id=resolved_id)
    return payload


def draft_report(
    project_root: Path,
    *,
    report_id: str,
    new_version: bool = False,
    evidence: str | None = None,
) -> dict[str, Any]:
    root = project_root.resolve()
    directory = _report_dir(root, _report_id(report_id))
    report = _load_report(directory)
    if evidence is not None:
        report["evidence_mode"] = _validate_evidence_mode(evidence)
    current = _ensure_version(report, new_version=new_version)
    version_dir = directory / "versions" / current
    (version_dir / "sections").mkdir(parents=True, exist_ok=True)
    evidence_payload = collect_evidence(root, report_id=report["report_id"])
    include_internal = report.get("evidence_mode") == "linked"
    references = build_reference_registry(evidence_payload, include_internal=include_internal, version=current)
    evidence_payload["citations"] = {
        "schema_version": references["schema_version"],
        "registry": f"reports/{report['report_id']}/references.json",
        "bibliography": references["bibliography"],
        "include_internal": include_internal,
    }
    write_json(directory / "evidence.json", evidence_payload)
    write_json(directory / "references.json", references)
    if include_internal:
        (version_dir / "references.bib").write_text(render_bibtex(references), encoding="utf-8")
    else:
        _remove_if_exists(version_dir / "references.bib")
    (version_dir / "main.tex").write_text(render_report(root, report, evidence_payload, references=references), encoding="utf-8")
    _update_version_paths(report, current, include_internal=include_internal)
    report["updated_at"] = now_iso()
    write_json(directory / "report.json", report)
    _append_revision(directory, f"drafted {current} ({report.get('evidence_mode')} evidence mode)")
    _write_report_index(root)
    return {
        "report_id": report["report_id"],
        "version": current,
        "main_tex": f"reports/{report['report_id']}/versions/{current}/main.tex",
        "references": f"reports/{report['report_id']}/references.json",
        "references_bib": f"reports/{report['report_id']}/versions/{current}/references.bib" if include_internal else "",
        "evidence": f"reports/{report['report_id']}/evidence.json",
        "evidence_mode": report.get("evidence_mode"),
    }


def configure_report(project_root: Path, *, report_id: str, evidence: str) -> dict[str, Any]:
    root = project_root.resolve()
    directory = _report_dir(root, _report_id(report_id))
    report = _load_report(directory)
    report["evidence_mode"] = _validate_evidence_mode(evidence)
    report["updated_at"] = now_iso()
    write_json(directory / "report.json", report)
    _append_revision(directory, f"configured evidence mode to {report['evidence_mode']}")
    _write_report_index(root)
    return {"report_id": report["report_id"], "evidence_mode": report["evidence_mode"]}


def add_feedback(project_root: Path, *, report_id: str, section: str, text: str) -> dict[str, Any]:
    root = project_root.resolve()
    directory = _report_dir(root, _report_id(report_id))
    report = _load_report(directory)
    row = {"timestamp": now_iso(), "report_id": report["report_id"], "section": section, "text": text}
    append_jsonl(directory / "feedback.jsonl", row)
    with (directory / "feedback.md").open("a", encoding="utf-8") as handle:
        handle.write(f"\n## {row['timestamp']} - {section}\n\n{text.strip()}\n")
    _append_revision(directory, f"recorded feedback for {section}")
    return {"report_id": report["report_id"], "feedback": "feedback.md", "section": section}


def build_report(project_root: Path, *, report_id: str, engine: str = "auto") -> dict[str, Any]:
    root = project_root.resolve()
    directory = _report_dir(root, _report_id(report_id))
    report = _load_report(directory)
    version = str(report.get("current_version") or "v001")
    result = build_latex(directory / "versions" / version, engine=engine)
    result.update(
        {
            "report_id": report["report_id"],
            "version": version,
            "main_tex": f"reports/{report['report_id']}/versions/{version}/main.tex",
            "pdf": f"reports/{report['report_id']}/versions/{version}/main.pdf" if result.get("ok") else "",
        }
    )
    if result.get("ok"):
        _append_revision(directory, f"built {version} with {result.get('engine')}")
    return result


def _new_report_payload(
    *,
    report_id: str,
    title: str,
    template: str,
    style: str,
    profile: dict[str, Any],
    evidence_mode: str,
    now: str,
) -> dict[str, Any]:
    return {
        "schema_version": REPORT_SCHEMA,
        "report_id": report_id,
        "title": title,
        "template": template,
        "style": style,
        "style_profile": profile,
        "evidence_mode": evidence_mode,
        "created_at": now,
        "updated_at": now,
        "current_version": "v001",
        "versions": [{"version": "v001", "created_at": now, "source_dir": "versions/v001", "main_tex": "versions/v001/main.tex"}],
        "paths": {
            "directory": f"reports/{report_id}",
            "report": "report.json",
            "evidence": "evidence.json",
            "template_lock": "template.lock.json",
            "feedback": "feedback.md",
            "revision_log": "REVISION_LOG.md",
        },
    }


def _ensure_version(report: dict[str, Any], *, new_version: bool) -> str:
    current = str(report.get("current_version") or "v001")
    if not new_version:
        return current
    current = _next_version(report)
    report.setdefault("versions", []).append(
        {
            "version": current,
            "created_at": now_iso(),
            "source_dir": f"versions/{current}",
            "main_tex": f"versions/{current}/main.tex",
        }
    )
    report["current_version"] = current
    return current


def _update_version_paths(report: dict[str, Any], version: str, *, include_internal: bool) -> None:
    for row in report.get("versions", []):
        if not isinstance(row, dict) or row.get("version") != version:
            continue
        row["main_tex"] = f"versions/{version}/main.tex"
        row["references"] = "references.json"
        if include_internal:
            row["references_bib"] = f"versions/{version}/references.bib"
        else:
            row.pop("references_bib", None)
        return


def _compact_report(root: Path, report: dict[str, Any]) -> dict[str, Any]:
    report_id = str(report.get("report_id") or "")
    version = str(report.get("current_version") or "")
    version_dir = _report_dir(root, report_id) / "versions" / version if report_id and version else None
    return {
        "report_id": report_id,
        "title": report.get("title"),
        "template": report.get("template"),
        "style": report.get("style"),
        "evidence_mode": report.get("evidence_mode"),
        "current_version": version,
        "updated_at": report.get("updated_at"),
        "main_tex": f"reports/{report_id}/versions/{version}/main.tex" if report_id and version else "",
        "pdf_exists": bool(version_dir and (version_dir / "main.pdf").exists()),
    }


def _write_report_index(root: Path) -> None:
    index = root / "reports" / "REPORT_INDEX.jsonl"
    index.parent.mkdir(parents=True, exist_ok=True)
    with index.open("w", encoding="utf-8") as handle:
        for report in list_reports(root):
            row = {
                "schema_version": REPORT_INDEX_SCHEMA,
                "report_id": report.get("report_id"),
                "title": report.get("title"),
                "template": report.get("template"),
                "style": report.get("style"),
                "evidence_mode": report.get("evidence_mode"),
                "current_version": report.get("current_version"),
                "updated_at": report.get("updated_at"),
                "path": f"reports/{report.get('report_id')}/report.json",
            }
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def _write_writing_plan(directory: Path, report: dict[str, Any]) -> None:
    profile = report.get("style_profile") if isinstance(report.get("style_profile"), dict) else {}
    sections = "\n".join(f"- {item}" for item in profile.get("sections", []))
    text = (
        f"# Writing Plan\n\n"
        f"Report: `{report.get('report_id')}`\n\n"
        f"Template: `{report.get('template')}`\n\n"
        f"Style: `{report.get('style')}` - {profile.get('emphasis', '')}\n\n"
        f"## Sections\n\n{sections}\n"
    )
    (directory / "writing_plan.md").write_text(text, encoding="utf-8")


def _default_title(root: Path) -> str:
    status = parse_status(root / "STATUS.md")
    if str(status.get("verified_positive_result") or "").strip():
        return "A Verified Iteris Report"
    target = str(status.get("target_artifact") or "")
    match = re.search(r"^#\s+(.+)$", read_project_text(root, target, limit=1000), flags=re.MULTILINE)
    return match.group(1).strip() if match else f"Iteris Report for {root.name}"


def _default_report_id(root: Path, title: str | None) -> str:
    base = title or _default_title(root) or root.name
    return f"{slugify(base, 44)}-{now_stamp()[:8].lower()}"


def _load_report(directory: Path) -> dict[str, Any]:
    report = read_json(directory / "report.json", default={})
    if not isinstance(report, dict) or report.get("schema_version") != REPORT_SCHEMA:
        raise FileNotFoundError(f"report not found: {directory / 'report.json'}")
    return report


def _append_revision(directory: Path, text: str) -> None:
    with (directory / "REVISION_LOG.md").open("a", encoding="utf-8") as handle:
        handle.write(f"- {now_iso()}: {text}.\n")


def _next_version(report: dict[str, Any]) -> str:
    versions = [str(item.get("version")) for item in report.get("versions", []) if isinstance(item, dict)]
    nums = [int(match.group(1)) for version in versions if (match := re.fullmatch(r"v(\d+)", version))]
    return f"v{(max(nums) if nums else 0) + 1:03d}"


def _report_id(value: str) -> str:
    if not value or not value.strip():
        raise ValueError("report id cannot be empty")
    return slugify(value, 64)


def _report_dir(root: Path, report_id: str) -> Path:
    return root / "reports" / report_id


def _validate_evidence_mode(value: str) -> str:
    if value not in SUPPORTED_EVIDENCE_MODES:
        raise ValueError(f"unsupported evidence mode: {value}; choose linked or portable")
    return value


def _write_if_missing(path: Path, text: str) -> None:
    if not path.exists():
        path.write_text(text, encoding="utf-8")


def _remove_if_exists(path: Path) -> None:
    if path.exists():
        path.unlink()

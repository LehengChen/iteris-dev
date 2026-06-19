"""Export built report artifacts for sharing and Overleaf upload."""

from __future__ import annotations

import json
import re
import shutil
import zipfile
from pathlib import Path
from typing import Any

from iteris.project import now_iso, read_json, slugify
from iteris.reporting.core import REPORT_SCHEMA

EXPORT_SCHEMA = "iteris.report_export.v0"
EXPORT_KINDS = {"pdf", "source-zip"}

_EXCLUDED_SOURCE_NAMES = {
    "main.pdf",
    "evidence.json",
    "references.json",
    "template.assets.json",
    "template.lock.json",
}
_EXCLUDED_SOURCE_SUFFIXES = (
    ".aux",
    ".blg",
    ".fdb_latexmk",
    ".fls",
    ".log",
    ".out",
    ".synctex.gz",
)


def export_report(
    project_root: Path,
    *,
    report_id: str,
    version: str = "",
    kind: str = "source-zip",
    output: Path | None = None,
) -> dict[str, Any]:
    root = project_root.resolve()
    kind = _validate_kind(kind)
    report_id = _report_id(report_id)
    report = _load_report(root, report_id)
    selected = _select_version(report, version)
    version_dir = root / "reports" / report_id / "versions" / selected
    if not version_dir.is_dir():
        raise FileNotFoundError(f"report version not found: {report_id}/{selected}")
    destination = _destination(root, report_id, selected, kind, output)
    if kind == "pdf":
        return _export_pdf(version_dir, destination, report_id=report_id, version=selected)
    return _export_source_zip(version_dir, destination, report_id=report_id, version=selected, report=report)


def _export_pdf(version_dir: Path, destination: Path, *, report_id: str, version: str) -> dict[str, Any]:
    source = version_dir / "main.pdf"
    if not source.is_file():
        raise FileNotFoundError(f"PDF has not been built: {source}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, destination)
    return _payload(report_id, version, "pdf", destination, ["main.pdf"], "application/pdf")


def _export_source_zip(
    version_dir: Path,
    destination: Path,
    *,
    report_id: str,
    version: str,
    report: dict[str, Any],
) -> dict[str, Any]:
    if not (version_dir / "main.tex").is_file():
        raise FileNotFoundError(f"LaTeX source has not been drafted: {version_dir / 'main.tex'}")
    files = _source_files(version_dir)
    names = [_arcname(version_dir, path) for path in files]
    destination.parent.mkdir(parents=True, exist_ok=True)
    manifest = _manifest(report_id, version, report, names)
    with zipfile.ZipFile(destination, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path, name in zip(files, names, strict=True):
            archive.write(path, name)
        archive.writestr("EXPORT_MANIFEST.json", json.dumps(manifest, indent=2, ensure_ascii=False) + "\n")
        archive.writestr("README.md", _readme(report_id, version, report))
    return _payload(
        report_id,
        version,
        "source-zip",
        destination,
        names + ["EXPORT_MANIFEST.json", "README.md"],
        "application/zip",
    )


def _source_files(version_dir: Path) -> list[Path]:
    files: list[Path] = []
    for path in sorted(version_dir.rglob("*")):
        if not path.is_file() or _skip_source_file(path):
            continue
        files.append(path)
    return files


def _skip_source_file(path: Path) -> bool:
    name = path.name
    return name.startswith(".") or name in _EXCLUDED_SOURCE_NAMES or name.endswith(_EXCLUDED_SOURCE_SUFFIXES)


def _arcname(version_dir: Path, path: Path) -> str:
    return path.relative_to(version_dir).as_posix()


def _manifest(report_id: str, version: str, report: dict[str, Any], files: list[str]) -> dict[str, Any]:
    return {
        "schema_version": EXPORT_SCHEMA,
        "generated_at": now_iso(),
        "report_id": report_id,
        "version": version,
        "kind": "source-zip",
        "entrypoint": "main.tex",
        "title": report.get("title"),
        "template": report.get("template"),
        "style": report.get("style"),
        "evidence_mode": report.get("evidence_mode"),
        "files": files,
        "omitted": sorted(_EXCLUDED_SOURCE_NAMES),
    }


def _readme(report_id: str, version: str, report: dict[str, Any]) -> str:
    title = str(report.get("title") or report_id)
    return (
        f"# {title}\n\n"
        f"Report: `{report_id}`\n\n"
        f"Version: `{version}`\n\n"
        "Entrypoint: `main.tex`\n\n"
        "Upload this ZIP to Overleaf with **New Project -> Upload Project**. "
        "The package contains LaTeX source files for this report version and omits "
        "Iteris audit JSON files such as `evidence.json` and `references.json`.\n"
    )


def _payload(
    report_id: str,
    version: str,
    kind: str,
    destination: Path,
    files: list[str],
    content_type: str,
) -> dict[str, Any]:
    return {
        "schema_version": EXPORT_SCHEMA,
        "report_id": report_id,
        "version": version,
        "kind": kind,
        "output": str(destination),
        "download_name": _download_name(report_id, version, kind),
        "content_type": content_type,
        "bytes": destination.stat().st_size,
        "files": files,
    }


def _destination(root: Path, report_id: str, version: str, kind: str, output: Path | None) -> Path:
    if output is not None:
        return output.expanduser().resolve()
    return root / "reports" / report_id / "exports" / _download_name(report_id, version, kind)


def _download_name(report_id: str, version: str, kind: str) -> str:
    ext = "pdf" if kind == "pdf" else "zip"
    suffix = "source" if kind == "source-zip" else "report"
    return f"{report_id}-{version}-{suffix}.{ext}"


def _load_report(root: Path, report_id: str) -> dict[str, Any]:
    report = read_json(root / "reports" / report_id / "report.json", default={})
    if not isinstance(report, dict) or report.get("schema_version") != REPORT_SCHEMA:
        raise FileNotFoundError(f"report not found: reports/{report_id}")
    return report


def _select_version(report: dict[str, Any], requested: str) -> str:
    versions = {str(item.get("version")) for item in report.get("versions", []) if isinstance(item, dict)}
    selected = requested or str(report.get("current_version") or "")
    if selected not in versions:
        raise FileNotFoundError(f"report version not found: {selected or '(missing)'}")
    return selected


def _validate_kind(value: str) -> str:
    if value not in EXPORT_KINDS:
        raise ValueError(f"unsupported export kind: {value}; choose pdf or source-zip")
    return value


def _report_id(value: str) -> str:
    value = str(value or "").strip()
    if not value or value != slugify(value, 64):
        raise ValueError("invalid report id")
    return value

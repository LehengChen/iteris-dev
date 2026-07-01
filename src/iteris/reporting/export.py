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
    ".bbl",
    ".bcf",
    ".aux",
    ".blg",
    ".fdb_latexmk",
    ".fls",
    ".log",
    ".out",
    ".run.xml",
    ".synctex.gz",
    ".zip",
)


def export_report(
    project_root: Path,
    *,
    report_id: str,
    version: str = "",
    kind: str = "source-zip",
    include_references: bool = True,
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
    destination = _destination(root, report_id, selected, kind, output, include_references=include_references)
    if kind == "pdf":
        return _export_pdf(version_dir, destination, report_id=report_id, version=selected)
    return _export_source_zip(
        version_dir,
        destination,
        report_id=report_id,
        version=selected,
        report=report,
        include_references=include_references,
    )


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
    include_references: bool,
) -> dict[str, Any]:
    if not (version_dir / "main.tex").is_file():
        raise FileNotFoundError(f"LaTeX source has not been drafted: {version_dir / 'main.tex'}")
    destination = destination.resolve()
    if destination == version_dir or _is_relative_to(destination, version_dir):
        raise ValueError("export output cannot be inside the report version directory")
    internal_keys, internal_bibliographies = _internal_reference_metadata(version_dir)
    files = _source_files(
        version_dir,
        include_references=include_references,
        destination=destination,
        internal_bibliographies=internal_bibliographies,
    )
    names = [_arcname(version_dir, path) for path in files]
    destination.parent.mkdir(parents=True, exist_ok=True)
    manifest = _manifest(report_id, version, report, names, include_references=include_references)
    with zipfile.ZipFile(destination, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path, name in zip(files, names, strict=True):
            if path.suffix == ".tex" and not include_references:
                archive.writestr(
                    name,
                    _without_internal_references(
                        path.read_text(encoding="utf-8", errors="replace"),
                        internal_keys=internal_keys,
                        internal_bibliographies=internal_bibliographies,
                    ),
                )
            else:
                archive.write(path, name)
        archive.writestr("EXPORT_MANIFEST.json", json.dumps(manifest, indent=2, ensure_ascii=False) + "\n")
        archive.writestr("README.md", _readme(report_id, version, report, include_references=include_references))
    return _payload(
        report_id,
        version,
        "source-zip",
        destination,
        names + ["EXPORT_MANIFEST.json", "README.md"],
        "application/zip",
        include_references=include_references,
    )


def _source_files(
    version_dir: Path,
    *,
    include_references: bool,
    destination: Path,
    internal_bibliographies: set[str],
) -> list[Path]:
    files: list[Path] = []
    for path in sorted(version_dir.rglob("*")):
        if not path.is_file() or path.resolve() == destination:
            continue
        if _skip_source_file(
            version_dir,
            path,
            include_references=include_references,
            internal_bibliographies=internal_bibliographies,
        ):
            continue
        files.append(path)
    return files


def _skip_source_file(
    version_dir: Path,
    path: Path,
    *,
    include_references: bool,
    internal_bibliographies: set[str],
) -> bool:
    name = path.name
    rel = path.relative_to(version_dir).as_posix()
    if not include_references and rel in internal_bibliographies:
        return True
    return name.startswith(".") or rel in _EXCLUDED_SOURCE_NAMES or name.endswith(_EXCLUDED_SOURCE_SUFFIXES)


def _arcname(version_dir: Path, path: Path) -> str:
    return path.relative_to(version_dir).as_posix()


def _manifest(
    report_id: str,
    version: str,
    report: dict[str, Any],
    files: list[str],
    *,
    include_references: bool,
) -> dict[str, Any]:
    return {
        "schema_version": EXPORT_SCHEMA,
        "generated_at": now_iso(),
        "report_id": report_id,
        "version": version,
        "kind": "source-zip",
        "references": "included" if include_references else "omitted",
        "entrypoint": "main.tex",
        "title": report.get("title"),
        "template": report.get("template"),
        "style": report.get("style"),
        "evidence_mode": report.get("evidence_mode"),
        "files": files,
        "omitted": sorted(_EXCLUDED_SOURCE_NAMES),
    }


def _readme(report_id: str, version: str, report: dict[str, Any], *, include_references: bool) -> str:
    title = str(report.get("title") or report_id)
    references = (
        "This package preserves the report bibliography and LaTeX citation commands, including Iteris-internal evidence references."
        if include_references
        else "This package omits Iteris-internal evidence references and the generated evidence-register appendix. External literature references are preserved when they use separate citation keys and bibliography files."
    )
    return (
        f"# {title}\n\n"
        f"Report: `{report_id}`\n\n"
        f"Version: `{version}`\n\n"
        "Entrypoint: `main.tex`\n\n"
        "Upload this ZIP to Overleaf with **New Project -> Upload Project**. "
        "The package contains LaTeX source files for this report version and omits "
        "Iteris audit JSON files such as `evidence.json` and `references.json`.\n\n"
        f"{references}\n"
    )


def _payload(
    report_id: str,
    version: str,
    kind: str,
    destination: Path,
    files: list[str],
    content_type: str,
    include_references: bool | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "schema_version": EXPORT_SCHEMA,
        "report_id": report_id,
        "version": version,
        "kind": kind,
        "output": str(destination),
        "download_name": _download_name(
            report_id,
            version,
            kind,
            include_references=True if include_references is None else include_references,
        ),
        "content_type": content_type,
        "bytes": destination.stat().st_size,
        "files": files,
    }
    if include_references is not None:
        payload["references"] = "included" if include_references else "omitted"
    return payload


def _destination(
    root: Path,
    report_id: str,
    version: str,
    kind: str,
    output: Path | None,
    *,
    include_references: bool,
) -> Path:
    if output is not None:
        return output.expanduser().resolve()
    return root / "reports" / report_id / "exports" / _download_name(
        report_id,
        version,
        kind,
        include_references=include_references,
    )


def _download_name(report_id: str, version: str, kind: str, *, include_references: bool = True) -> str:
    ext = "pdf" if kind == "pdf" else "zip"
    if kind == "source-zip":
        suffix = "source" if include_references else "source-no-internal-refs"
    else:
        suffix = "report"
    return f"{report_id}-{version}-{suffix}.{ext}"


def _without_internal_references(text: str, *, internal_keys: set[str], internal_bibliographies: set[str]) -> str:
    text = _remove_generated_evidence_appendix(text)
    text = re.sub(
        r"\\(?P<command>[A-Za-z]*[Cc]ite[A-Za-z]*\*?|nocite|cite\w*\*?)(?P<opts>(?:\s*\[[^\]]*\]){0,2})\s*\{(?P<keys>[^}]*)\}",
        lambda match: _filter_citation_command(match, internal_keys),
        text,
    )
    text = re.sub(
        r"\n?\\begingroup\s*\\sloppy\s*\\bibliographystyle\{(?P<style>[^}]*)\}\s*\\bibliography\{(?P<bibs>[^}]*)\}\s*\\endgroup\s*\n?",
        lambda match: _filter_grouped_bibliography(match, internal_bibliographies),
        text,
        flags=re.DOTALL,
    )
    text = re.sub(
        r"^(?P<prefix>\s*)\\bibliography\{(?P<bibs>[^}]*)\}\s*$",
        lambda match: _filter_bibliography_command(match, internal_bibliographies),
        text,
        flags=re.MULTILINE,
    )
    text = re.sub(
        r"^\s*\\addbibresource(?:\[[^\]]*\])?\{(?P<bib>[^}]*)\}\s*$",
        lambda match: "" if _bib_file_name(match.group("bib")) in internal_bibliographies else match.group(0),
        text,
        flags=re.MULTILINE,
    )
    if "\\bibliography{" not in text:
        text = re.sub(r"^\s*\\bibliographystyle\{[^}]*\}\s*$", "", text, flags=re.MULTILINE)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text


def _filter_citation_command(match: re.Match[str], internal_keys: set[str]) -> str:
    keys = [key.strip() for key in match.group("keys").split(",") if key.strip()]
    external = [key for key in keys if key not in internal_keys]
    if len(external) == len(keys):
        return match.group(0)
    if not external:
        return ""
    return f"\\{match.group('command')}{match.group('opts')}{{{', '.join(external)}}}"


def _filter_grouped_bibliography(match: re.Match[str], internal_bibliographies: set[str]) -> str:
    external = _external_bibliography_names(match.group("bibs"), internal_bibliographies)
    if not external:
        return "\n"
    return (
        "\n\\begingroup\n"
        "\\sloppy\n"
        f"\\bibliographystyle{{{match.group('style')}}}\n"
        f"\\bibliography{{{','.join(external)}}}\n"
        "\\endgroup\n"
    )


def _filter_bibliography_command(match: re.Match[str], internal_bibliographies: set[str]) -> str:
    external = _external_bibliography_names(match.group("bibs"), internal_bibliographies)
    if not external:
        return ""
    return f"{match.group('prefix')}\\bibliography{{{','.join(external)}}}"


def _external_bibliography_names(raw: str, internal_bibliographies: set[str]) -> list[str]:
    out = []
    for item in raw.split(","):
        name = item.strip()
        if not name:
            continue
        if _bib_file_name(name) in internal_bibliographies:
            continue
        out.append(name)
    return out


def _bib_file_name(value: str) -> str:
    name = Path(value.replace("\\", "/")).name
    return name if name.endswith(".bib") else f"{name}.bib"


def _internal_reference_metadata(version_dir: Path) -> tuple[set[str], set[str]]:
    registry = read_json(version_dir / "references.json", default={})
    keys: set[str] = set()
    bibliographies: set[str] = set()
    if isinstance(registry, dict):
        entries = registry.get("entries")
        for entry in entries if isinstance(entries, list) else []:
            if isinstance(entry, dict) and entry.get("key"):
                keys.add(str(entry["key"]))
        bibliography = str(registry.get("bibliography") or "")
        if bibliography:
            bibliographies.add(_bib_file_name(bibliography))
    return keys, bibliographies


def _remove_generated_evidence_appendix(text: str) -> str:
    marker = "\n\\appendix\n\\section{Evidence Register}\n"
    idx = text.find(marker)
    if idx < 0:
        return text
    bib = re.search(r"\n\\begingroup\s*\\sloppy\s*\\bibliographystyle", text[idx:], flags=re.DOTALL)
    if not bib:
        return text[:idx].rstrip() + "\n"
    return text[:idx].rstrip() + "\n" + text[idx + bib.start() :]


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


def _is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


def _report_id(value: str) -> str:
    value = str(value or "").strip()
    if not value or value != slugify(value, 64):
        raise ValueError("invalid report id")
    return value

"""Project-local arXiv reference fetching."""

from __future__ import annotations

import gzip
import io
import re
import tarfile
from pathlib import Path
from typing import Any

import requests

from iteris.project import now_iso, slugify, write_json


def normalize_arxiv_id(raw: str) -> str:
    text = raw.strip()
    text = re.sub(r"^arxiv:", "", text, flags=re.IGNORECASE)
    for marker in ("/abs/", "/pdf/", "/e-print/"):
        if marker in text:
            text = text.rsplit(marker, 1)[1]
            break
    text = text.split("?", 1)[0].split("#", 1)[0]
    if text.endswith(".pdf"):
        text = text[:-4]
    return text.strip("/")


def fetch_arxiv_reference(
    project_root: Path,
    *,
    arxiv_id: str,
    timeout_seconds: int = 60,
    include_pdf: bool = False,
) -> dict[str, Any]:
    """Fetch arXiv source first, falling back to PDF text extraction."""
    normalized = normalize_arxiv_id(arxiv_id)
    if not normalized:
        raise ValueError("arxiv_id is empty")
    safe_id = slugify(normalized.replace("/", "-"), limit=80)
    reference_dir = project_root / "artifacts" / "references" / "arxiv" / safe_id
    reference_dir.mkdir(parents=True, exist_ok=True)

    source_url = f"https://arxiv.org/e-print/{normalized}"
    pdf_url = f"https://arxiv.org/pdf/{normalized}.pdf"
    source = _fetch_source(source_url, reference_dir, timeout_seconds=timeout_seconds)
    pdf: dict[str, Any] | None = None
    if include_pdf or not source.get("ok"):
        pdf = _fetch_pdf_text(pdf_url, reference_dir, timeout_seconds=timeout_seconds)

    primary_paths = list(source.get("source_files") or [])
    if not primary_paths and pdf and pdf.get("text_path"):
        primary_paths.append(str(pdf["text_path"]))

    manifest_path = reference_dir / "manifest.json"
    manifest = {
        "schema_version": "iteris.arxiv_reference.v0",
        "arxiv_id": normalized,
        "created_at": now_iso(),
        "retrieval_policy": "arxiv_source_first_then_pdf_text_fallback",
        "source_url": source_url,
        "pdf_url": pdf_url,
        "reference_dir": str(reference_dir.relative_to(project_root)),
        "manifest_path": str(manifest_path.relative_to(project_root)),
        "primary_paths": primary_paths,
        "source": source,
        "pdf": pdf,
    }
    write_json(manifest_path, manifest)
    return manifest


def _fetch_source(url: str, reference_dir: Path, *, timeout_seconds: int) -> dict[str, Any]:
    result: dict[str, Any] = {"ok": False, "url": url, "source_files": [], "errors": []}
    try:
        response = requests.get(url, timeout=timeout_seconds)
    except requests.RequestException as exc:
        result["errors"].append(f"source request failed: {exc}")
        return result
    result["status_code"] = response.status_code
    if response.status_code >= 400:
        result["errors"].append(f"source request returned HTTP {response.status_code}")
        return result
    content = response.content
    if _looks_like_html(content):
        result["errors"].append("source response looked like HTML, not an arXiv source archive")
        return result

    raw_path = reference_dir / "source-eprint.raw"
    raw_path.write_bytes(content)
    result["raw_path"] = str(raw_path.relative_to(_project_root_from_reference_dir(reference_dir)))
    source_dir = reference_dir / "source"
    source_dir.mkdir(parents=True, exist_ok=True)

    files: list[Path] = []
    try:
        files = _extract_tar_bytes(content, source_dir)
        result["source_format"] = "tar"
    except tarfile.TarError:
        text = _decode_source_blob(content)
        if text is not None:
            suffix = ".tex" if _looks_like_latex(text) else ".txt"
            out = source_dir / f"source{suffix}"
            out.write_text(text, encoding="utf-8")
            files = [out]
            result["source_format"] = "single_file"
        else:
            result["errors"].append("source response was neither a tar archive nor decodable source text")

    result["source_files"] = [str(path.relative_to(_project_root_from_reference_dir(reference_dir))) for path in files]
    result["ok"] = bool(files)
    return result


def _fetch_pdf_text(url: str, reference_dir: Path, *, timeout_seconds: int) -> dict[str, Any]:
    result: dict[str, Any] = {"ok": False, "url": url, "errors": []}
    try:
        response = requests.get(url, timeout=timeout_seconds)
    except requests.RequestException as exc:
        result["errors"].append(f"pdf request failed: {exc}")
        return result
    result["status_code"] = response.status_code
    if response.status_code >= 400:
        result["errors"].append(f"pdf request returned HTTP {response.status_code}")
        return result
    content = response.content
    if not content.startswith(b"%PDF"):
        result["errors"].append("pdf response did not start with a PDF header")
        return result

    pdf_path = reference_dir / "paper.pdf"
    text_path = reference_dir / "paper.txt"
    pdf_path.write_bytes(content)
    project_root = _project_root_from_reference_dir(reference_dir)
    result["pdf_path"] = str(pdf_path.relative_to(project_root))
    try:
        text = extract_pdf_text(pdf_path)
    except RuntimeError as exc:
        result["errors"].append(str(exc))
        return result
    text_path.write_text(text, encoding="utf-8")
    result["text_path"] = str(text_path.relative_to(project_root))
    result["ok"] = bool(text.strip())
    if not result["ok"]:
        result["errors"].append("PDF text extraction returned empty text")
    return result


def extract_pdf_text(pdf_path: Path) -> str:
    try:
        from pdfminer.high_level import extract_pages
        from pdfminer.layout import LTTextContainer
    except ModuleNotFoundError as exc:
        raise RuntimeError("pdfminer.six is not installed; install the `pdfminer.six` package or reinstall Iteris dependencies") from exc
    chunks: list[str] = []
    for page_index, page_layout in enumerate(extract_pages(str(pdf_path)), start=1):
        chunks.append(f"\n\n--- page {page_index} ---\n")
        for element in page_layout:
            if isinstance(element, LTTextContainer):
                chunks.append(element.get_text())
    return "".join(chunks).strip() + "\n"


def _extract_tar_bytes(content: bytes, dest: Path) -> list[Path]:
    extracted: list[Path] = []
    with tarfile.open(fileobj=io.BytesIO(content), mode="r:*") as archive:
        dest_resolved = dest.resolve()
        for member in archive.getmembers():
            if member.isdir():
                continue
            if member.issym() or member.islnk():
                raise tarfile.TarError(f"unsafe tar link member: {member.name}")
            target = (dest / member.name).resolve()
            try:
                target.relative_to(dest_resolved)
            except ValueError as exc:
                raise tarfile.TarError(f"unsafe tar member path: {member.name}") from exc
            if member.name.startswith("/") or ".." in Path(member.name).parts:
                raise tarfile.TarError(f"unsafe tar member path: {member.name}")
            archive.extract(member, dest)
            extracted.append(target)
    return extracted


def _project_root_from_reference_dir(reference_dir: Path) -> Path:
    return reference_dir.parents[3]


def _decode_source_blob(content: bytes) -> str | None:
    candidates = [content]
    try:
        candidates.append(gzip.decompress(content))
    except OSError:
        pass
    for candidate in candidates:
        try:
            text = candidate.decode("utf-8")
        except UnicodeDecodeError:
            try:
                text = candidate.decode("latin-1")
            except UnicodeDecodeError:
                continue
        if "\x00" not in text:
            return text
    return None


def _looks_like_html(content: bytes) -> bool:
    head = content[:300].lstrip().lower()
    return head.startswith(b"<!doctype html") or head.startswith(b"<html")


def _looks_like_latex(text: str) -> bool:
    return "\\documentclass" in text or "\\begin{" in text or "\\newcommand" in text

"""BibTeX citation registry for Iteris report evidence."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from iteris.project import now_iso, slugify
from iteris.reporting.latex import latex_escape

REFERENCE_SCHEMA = "iteris.report_references.v0"


@dataclass(frozen=True)
class BibTeXEntry:
    citation_key: str
    entry_type: str
    fields: dict[str, str]
    kind: str = "artifact"
    role: str = ""
    fact_id: str = ""
    request_id: str = ""
    path: str = ""


def build_reference_registry(
    evidence: dict[str, Any],
    *,
    include_internal: bool,
    version: str = "",
) -> dict[str, Any]:
    generated_at = now_iso()
    report_id = str(evidence.get("report_id") or "")
    registry: dict[str, Any] = {
        "schema_version": REFERENCE_SCHEMA,
        "generated_at": generated_at,
        "report_id": report_id,
        "version": version,
        "style": "amsplain",
        "citation_style": "bibtex",
        "bibliography": _bibliography_path(report_id, version),
        "include_internal": include_internal,
        "keys": {"facts": {}, "verifications": {}, "artifacts": {}},
        "entries": [],
        "fact_labels": [],
        "sections": [],
        "fact_graph": _fact_graph(evidence),
    }
    if not include_internal:
        registry["omitted_reason"] = "portable evidence mode omits internal project citations"
        return registry

    entries = build_bibtex_entries(evidence, version=version)
    registry["entries"] = [_registry_entry(entry) for entry in entries]
    _index_keys(registry, entries)
    _index_fact_labels(registry, evidence)
    registry["sections"] = _section_citations(evidence, registry)
    return registry


def build_bibtex_entries(evidence: dict[str, Any], *, version: str = "") -> list[BibTeXEntry]:
    report_id = str(evidence.get("report_id") or "")
    generated = str(evidence.get("generated_at") or now_iso())
    year = _year(generated)
    answer = evidence.get("answer") if isinstance(evidence.get("answer"), dict) else {}
    entries: list[BibTeXEntry] = []
    seen: set[str] = set()

    evidence_path = _evidence_path(report_id)
    _append_entry(
        entries,
        seen,
        kind="evidence",
        role="evidence",
        semantic_id=f"evidence:{report_id}:{version}",
        citation_key=citation_key_for_evidence_register(report_id, version=version),
        title=f"Iteris evidence register for {report_id or 'report'}",
        howpublished=f"Project file: {evidence_path}",
        note="Machine-readable bridge from report text to checked facts, artifacts, and verification records.",
        year=year,
        path=evidence_path,
    )
    for role, path in _artifact_paths(evidence, answer):
        _append_entry(
            entries,
            seen,
            kind="artifact",
            role=role,
            semantic_id=f"path:{path}",
            citation_key=citation_key_for_artifact(path),
            title=f"Iteris project artifact: {role}",
            howpublished=f"Project file: {path}",
            note="Project-relative artifact cited by the report evidence registry.",
            year=year,
            path=path,
        )
    for role, request_id in _verification_ids(evidence, answer):
        path = _verification_path(request_id)
        _append_entry(
            entries,
            seen,
            kind="verification",
            role=role,
            semantic_id=f"verification:{request_id}",
            citation_key=citation_key_for_verification(request_id),
            title=f"Iteris verification record: {role}",
            howpublished=f"Project file: {path}",
            note=f"Request id: {request_id}",
            year=year,
            request_id=request_id,
            path=path,
        )
    facts = evidence.get("facts") if isinstance(evidence.get("facts"), list) else []
    for idx, fact in enumerate(facts, 1):
        if not isinstance(fact, dict):
            continue
        fact_id = str(fact.get("fact_id") or "")
        path = str(fact.get("path") or "")
        verification = str(fact.get("verification") or "")
        _append_entry(
            entries,
            seen,
            kind="fact",
            role="checked_fact",
            semantic_id=f"fact:{fact_id}",
            citation_key=citation_key_for_fact(fact_id),
            title=f"Iteris fact F{idx}: {fact.get('claim_summary') or fact_id}",
            howpublished=f"Project file: {path}",
            note=_fact_note(fact, verification),
            year=year,
            fact_id=fact_id,
            request_id=verification,
            path=path,
        )
    return entries


def bibtex_entries_from_evidence_file(evidence_path: Path) -> list[BibTeXEntry]:
    evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
    return build_bibtex_entries(evidence)


def render_bibtex(value: dict[str, Any] | list[BibTeXEntry]) -> str:
    entries = _entries_from_value(value)
    if not entries:
        return "% Generated by iteris report. No internal references emitted in portable mode.\n"
    chunks = ["% Generated by iteris report. Project file paths are relative."]
    chunks.extend(_bibtex_entry(entry) for entry in entries)
    return "\n\n".join(chunks).rstrip() + "\n"


def citation_key_for_fact(fact_id: str) -> str:
    return _semantic_key("itfact", fact_id.removeprefix("fact:"), fact_id)


def citation_key_for_verification(request_id: str) -> str:
    return _semantic_key("itver", request_id, request_id)


def citation_key_for_artifact(path: str) -> str:
    return _semantic_key("itpath", path.replace("/", "-"), path)


def citation_key_for_evidence_register(report_id: str, *, version: str = "") -> str:
    semantic = f"{report_id}:{version}" if version else report_id
    return _semantic_key("itevid", semantic or "report", semantic or "report")


def citation_keys(registry: dict[str, Any], *roles: str) -> list[str]:
    keys = registry.get("keys") if isinstance(registry.get("keys"), dict) else {}
    out: list[str] = []
    for role in roles:
        key = keys.get(role)
        if isinstance(key, str) and key:
            out.append(key)
    return out


def fact_citation_key(registry: dict[str, Any], fact_id: str) -> str:
    keys = registry.get("keys") if isinstance(registry.get("keys"), dict) else {}
    facts = keys.get("facts") if isinstance(keys.get("facts"), dict) else {}
    return str(facts.get(fact_id) or "")


def _append_entry(entries: list[BibTeXEntry], seen: set[str], **kwargs: Any) -> None:
    key = str(kwargs.pop("citation_key"))
    if not key or key in seen:
        return
    seen.add(key)
    entries.append(
        BibTeXEntry(
            citation_key=key,
            entry_type="misc",
            fields={
                "title": str(kwargs.pop("title")),
                "howpublished": str(kwargs.pop("howpublished")),
                "note": str(kwargs.pop("note")),
                "year": str(kwargs.pop("year")),
            },
            kind=str(kwargs.pop("kind")),
            role=str(kwargs.pop("role")),
            fact_id=str(kwargs.pop("fact_id", "")),
            request_id=str(kwargs.pop("request_id", "")),
            path=str(kwargs.pop("path", "")),
        )
    )


def _entries_from_value(value: dict[str, Any] | list[BibTeXEntry]) -> list[BibTeXEntry]:
    if isinstance(value, list):
        return value
    raw_entries = value.get("entries") if isinstance(value, dict) else []
    entries: list[BibTeXEntry] = []
    for raw in raw_entries if isinstance(raw_entries, list) else []:
        if not isinstance(raw, dict):
            continue
        entries.append(
            BibTeXEntry(
                citation_key=str(raw.get("key") or ""),
                entry_type=str(raw.get("entry_type") or "misc"),
                fields={str(k): str(v) for k, v in (raw.get("fields") or {}).items()},
                kind=str(raw.get("kind") or ""),
                role=str(raw.get("role") or ""),
                fact_id=str(raw.get("fact_id") or ""),
                request_id=str(raw.get("request_id") or ""),
                path=str(raw.get("path") or ""),
            )
        )
    return [entry for entry in entries if entry.citation_key]


def _registry_entry(entry: BibTeXEntry) -> dict[str, Any]:
    return {
        "key": entry.citation_key,
        "entry_type": entry.entry_type,
        "kind": entry.kind,
        "role": entry.role,
        "fact_id": entry.fact_id,
        "request_id": entry.request_id,
        "path": entry.path,
        "fields": entry.fields,
    }


def _index_keys(registry: dict[str, Any], entries: list[BibTeXEntry]) -> None:
    keys = registry["keys"]
    for entry in entries:
        if entry.kind == "fact" and entry.fact_id:
            keys["facts"][entry.fact_id] = entry.citation_key
        elif entry.kind == "verification" and entry.request_id:
            keys["verifications"][entry.request_id] = entry.citation_key
            keys.setdefault(entry.role, entry.citation_key)
        elif entry.kind == "artifact" and entry.path:
            keys["artifacts"][entry.path] = entry.citation_key
            keys.setdefault(entry.role, entry.citation_key)
        elif entry.kind == "evidence":
            keys["evidence"] = entry.citation_key


def _index_fact_labels(registry: dict[str, Any], evidence: dict[str, Any]) -> None:
    facts = evidence.get("facts") if isinstance(evidence.get("facts"), list) else []
    for idx, fact in enumerate(facts, 1):
        if not isinstance(fact, dict):
            continue
        fact_id = str(fact.get("fact_id") or "")
        registry["fact_labels"].append(
            {
                "label": f"F{idx}",
                "fact_id": fact_id,
                "citation_key": fact_citation_key(registry, fact_id),
                "path": str(fact.get("path") or ""),
                "verification": str(fact.get("verification") or ""),
            }
        )


def _section_citations(evidence: dict[str, Any], registry: dict[str, Any]) -> list[dict[str, Any]]:
    sections = evidence.get("sections") if isinstance(evidence.get("sections"), list) else []
    keys = registry.get("keys") if isinstance(registry.get("keys"), dict) else {}
    artifact_keys = keys.get("artifacts") if isinstance(keys.get("artifacts"), dict) else {}
    out = []
    for section in sections:
        uses = section.get("uses") if isinstance(section, dict) and isinstance(section.get("uses"), dict) else {}
        cite_keys = []
        for fact_id in uses.get("fact_ids") or []:
            key = fact_citation_key(registry, str(fact_id))
            if key:
                cite_keys.append(key)
        for path in uses.get("paths") or []:
            key = artifact_keys.get(str(path))
            if key:
                cite_keys.append(str(key))
        out.append({"section_id": section.get("section_id"), "cite_keys": cite_keys, "uses": uses})
    return out


def _artifact_paths(evidence: dict[str, Any], answer: dict[str, Any]) -> list[tuple[str, str]]:
    pairs: list[tuple[str, str]] = []
    if answer.get("target_artifact"):
        pairs.append(("target_artifact", str(answer["target_artifact"])))
    for record in evidence.get("source_paths") or []:
        if isinstance(record, dict) and record.get("path"):
            pairs.append((str(record.get("role") or "source_path"), str(record["path"])))
    for record in evidence.get("checked_artifacts") or []:
        if isinstance(record, dict) and record.get("path"):
            pairs.append(("checked_artifact", str(record["path"])))
    return _unique_pairs(pairs)


def _verification_ids(evidence: dict[str, Any], answer: dict[str, Any]) -> list[tuple[str, str]]:
    pairs = [
        ("goal_success_verification", str(answer.get("goal_success_verification") or "")),
        ("assembly_verification", str(answer.get("assembly_verification") or "")),
    ]
    for fact in evidence.get("facts") or []:
        if isinstance(fact, dict) and fact.get("verification"):
            pairs.append(("fact_verification", str(fact["verification"])))
    return _unique_pairs([(role, request_id) for role, request_id in pairs if request_id])


def _unique_pairs(pairs: list[tuple[str, str]]) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    seen: set[str] = set()
    for role, value in pairs:
        if not value or value in seen:
            continue
        seen.add(value)
        out.append((role, value))
    return out


def _bibtex_entry(entry: BibTeXEntry) -> str:
    fields = {
        "author": "{Iteris fact graph}",
        "title": _bib_value(entry.fields.get("title")),
        "year": _bib_value(entry.fields.get("year")),
        "howpublished": _howpublished_value(entry.fields.get("howpublished")),
        "note": _bib_value(entry.fields.get("note")),
    }
    body = ",\n".join(f"  {name} = {value}" for name, value in fields.items())
    return f"@{entry.entry_type}{{{entry.citation_key},\n{body}\n}}"


def _bib_value(value: Any) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    return "{" + latex_escape(text) + "}"


def _path_value(value: Any) -> str:
    text = str(value or "").replace("\\", "/").replace("{", "").replace("}", "")
    return text.replace("%", r"\%").replace("#", r"\#")


def _howpublished_value(value: Any) -> str:
    text = str(value or "")
    prefix = "Project file: "
    if text.startswith(prefix):
        return "{Project file: " + r"\path{" + _path_value(text[len(prefix) :]) + "}}"
    return "{" + r"\path{" + _path_value(text) + "}}"


def _semantic_key(prefix: str, slug_text: str, semantic_id: str) -> str:
    slug = re.sub(r"[^a-z0-9-]+", "-", slugify(slug_text, 44).lower())
    slug = re.sub(r"-+", "-", slug).strip("-")
    digest = hashlib.sha1(str(semantic_id or slug_text).encode("utf-8")).hexdigest()[:8]
    return f"{prefix}-{slug or 'item'}-{digest}"


def _fact_note(fact: dict[str, Any], verification: str) -> str:
    parts = [
        f"status: {fact.get('status') or 'unknown'}",
        f"review: {fact.get('review_level') or 'unknown'}",
    ]
    if verification:
        parts.append("verification link recorded in references.json")
    return "; ".join(parts)


def _verification_path(request_id: str) -> str:
    if not request_id:
        return ""
    if request_id.startswith("verification/"):
        return request_id
    return f"verification/results/{request_id}.json"


def _evidence_path(report_id: str) -> str:
    return f"reports/{report_id}/evidence.json" if report_id else "evidence.json"


def _bibliography_path(report_id: str, version: str) -> str:
    if report_id and version:
        return f"reports/{report_id}/versions/{version}/references.bib"
    return "references.bib"


def _fact_graph(evidence: dict[str, Any]) -> dict[str, Any]:
    graph = evidence.get("fact_graph") if isinstance(evidence.get("fact_graph"), dict) else {}
    return {
        "fact_index": graph.get("fact_index") or "memory/facts/FACT_INDEX.jsonl",
        "checked_fact_ids": list(graph.get("checked_fact_ids") or []),
    }


def _year(value: Any) -> str:
    match = re.search(r"\b(20\d{2}|19\d{2})\b", str(value or ""))
    return match.group(1) if match else now_iso()[:4]

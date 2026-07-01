"""Shared verified fact pool for family closure siblings."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from iteris.memory.family import family_index_path, load_family_index
from iteris.project import append_jsonl, now_iso, read_json

POOL_ENTRY_SCHEMA = "iteris.family_pool_entry.v1"
FAMILY_INDEX_LINE_SCHEMA = "iteris.family_index_line.v0"


def normalize_pool_row(row: dict[str, Any]) -> dict[str, Any]:
    """Unify closure export rows and evolve curator rows for display/search."""
    out = dict(row)
    schema = str(out.get("schema_version") or "")
    if schema == POOL_ENTRY_SCHEMA:
        out.setdefault("origin_fact_id", out.get("origin_fact_id") or out.get("fact_id"))
        out.setdefault("claim_summary", out.get("claim_summary") or out.get("curated_summary") or "")
        out.setdefault("source_project", out.get("source_project") or "")
        out.setdefault("source_sibling_id", out.get("source_sibling_id") or "")
        out.setdefault("usable_by", out.get("usable_by") or [])
    elif schema == FAMILY_INDEX_LINE_SCHEMA:
        out.setdefault("origin_fact_id", out.get("origin_fact_id"))
        out.setdefault("claim_summary", out.get("claim_summary") or out.get("curated_summary") or "")
    return out


def list_pool_entries(family_root: Path, *, sibling_id: str | None = None) -> list[dict[str, Any]]:
    rows = [normalize_pool_row(row) for row in load_family_index(family_root)]
    if sibling_id:
        filtered: list[dict[str, Any]] = []
        for row in rows:
            usable = row.get("usable_by") or []
            if not usable or sibling_id in [str(item) for item in usable]:
                filtered.append(row)
        return filtered
    return rows


def _fact_index_row(project: Path, fact_id: str) -> dict[str, Any] | None:
    index_path = project / "memory" / "facts" / "FACT_INDEX.jsonl"
    if not index_path.exists():
        return None
    for line in index_path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(row, dict) and str(row.get("fact_id")) == fact_id:
            return row
    return None


def export_verified_fact(
    family_root: Path,
    *,
    source_project: Path,
    fact_id: str,
    source_sibling_id: str,
    usable_by: list[str],
) -> dict[str, Any]:
    """Promote a sibling verified fact into the family shared pool."""
    family_root = family_root.resolve()
    source_project = source_project.resolve()
    row = _fact_index_row(source_project, fact_id)
    if row is None:
        raise ValueError(f"fact not found in FACT_INDEX: {fact_id}")
    if row.get("status") != "verified":
        raise ValueError(f"fact is not verified: {fact_id}")

    artifact = row.get("artifact") or row.get("path")
    if isinstance(artifact, Path):
        artifact = str(artifact)
    if isinstance(artifact, str) and artifact and not Path(artifact).is_absolute():
        artifact = str((source_project / artifact).relative_to(source_project))

    entry = {
        "schema_version": POOL_ENTRY_SCHEMA,
        "origin_fact_id": fact_id,
        "source_sibling_id": source_sibling_id,
        "source_project": source_project.name,
        "claim_summary": row.get("claim_summary") or row.get("claim") or "",
        "artifact": artifact,
        "usable_by": usable_by,
        "verification": row.get("verification"),
        "exported_at": now_iso(),
    }
    family_index_path(family_root).parent.mkdir(parents=True, exist_ok=True)
    append_jsonl(family_index_path(family_root), entry)
    return entry


def build_pool_context_block(family_root: Path, *, sibling_id: str | None = None, limit: int = 12) -> str:
    """Render a compact pool block for sibling goal prompts."""
    entries = list_pool_entries(family_root, sibling_id=sibling_id)
    if not entries:
        return ""
    lines = [
        "Family shared pool (reviewed leads from sibling projects — re-verify locally before cite):",
    ]
    for entry in entries[:limit]:
        origin = entry.get("origin_fact_id") or "(missing)"
        summary = str(entry.get("claim_summary") or entry.get("curated_summary") or "").strip()
        source = entry.get("source_sibling_id") or entry.get("source_project") or "?"
        usable = entry.get("usable_by") or []
        usable_text = f" usable_by={','.join(str(item) for item in usable)}" if usable else ""
        lines.append(f"- [{source}] `{origin}` — {summary}{usable_text}")
    if len(entries) > limit:
        lines.append(f"- ... and {len(entries) - limit} more pool entries")
    return "\n".join(lines)

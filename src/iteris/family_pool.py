"""Family closure shared pool: verified facts exportable across sibling loops."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from iteris.memory.family import family_index_path, load_family_index, upsert_family_entries
from iteris.memory.search import load_jsonl
from iteris.project import now_iso, project_id_from_path, read_json

FAMILY_POOL_ENTRY_SCHEMA = "iteris.family_pool_entry.v1"
LEGACY_POOL_SCHEMA = "iteris.family_fact.v0"


def pool_index_path(family_root: Path) -> Path:
    return family_index_path(family_root)


def load_pool(family_root: Path) -> list[dict[str, Any]]:
    return load_family_index(family_root)


def _normalize_pool_row(row: dict[str, Any]) -> dict[str, Any]:
    """Unify legacy closure exports and evolve-style index lines for search/display."""
    schema = str(row.get("schema_version") or "")
    if schema == LEGACY_POOL_SCHEMA:
        origin = str(row.get("fact_id") or "")
        return {
            "schema_version": FAMILY_POOL_ENTRY_SCHEMA,
            "origin_fact_id": origin,
            "source_sibling_id": row.get("source_project"),
            "source_project": row.get("source_project"),
            "fact_id": origin,
            "verification": row.get("verification"),
            "claim_summary": row.get("claim_summary") or "",
            "artifact": row.get("artifact"),
            "usable_by": row.get("usable_by") or [],
            "exported_at": row.get("exported_at"),
            "family_id": row.get("family_id"),
        }
    if schema == FAMILY_POOL_ENTRY_SCHEMA or row.get("origin_fact_id"):
        out = dict(row)
        out.setdefault("origin_fact_id", row.get("fact_id"))
        return out
    # evolve supervisor curated line — pass through
    return row


def applicable_pool_entries(
    family_root: Path,
    *,
    sibling_id: str | None = None,
    source_project: str | None = None,
    limit: int = 20,
) -> list[dict[str, Any]]:
    rows = [_normalize_pool_row(r) for r in load_pool(family_root)]
    out: list[dict[str, Any]] = []
    for row in rows:
        usable = row.get("usable_by") or []
        if sibling_id and usable and sibling_id not in usable and source_project not in usable:
            continue
        if source_project and str(row.get("source_project") or "") == source_project:
            continue
        out.append(row)
    return out[:limit]


def export_verified_fact(
    family_root: Path,
    sibling_root: Path,
    *,
    fact_id: str,
    sibling_id: str,
    usable_by: list[str] | None = None,
    verification: str | None = None,
) -> dict[str, Any]:
    """Promote a sibling verified fact into the family pool."""
    fact_id = fact_id.strip()
    if not fact_id.startswith("fact:"):
        raise ValueError(f"fact_id must start with fact: got {fact_id!r}")

    index_rows = load_jsonl(sibling_root / "memory" / "facts" / "FACT_INDEX.jsonl")
    meta = next((r for r in index_rows if r.get("fact_id") == fact_id), None)
    if not meta:
        raise ValueError(f"fact not in FACT_INDEX: {fact_id}")

    status = str(meta.get("status") or "")
    review = str(meta.get("review_level") or "")
    if status != "verified" and review != "verified":
        raise ValueError(f"fact {fact_id} is not verified (status={status!r})")

    claim_summary = str(meta.get("claim_summary") or meta.get("title") or "").strip()
    if not claim_summary:
        raise ValueError(f"fact {fact_id} has empty claim_summary")

    ver = verification or str(meta.get("verification") or "").strip()
    if not ver:
        raise ValueError(f"fact {fact_id} has no verification id")

    artifact = meta.get("artifact_path") or meta.get("path")
    entry: dict[str, Any] = {
        "schema_version": FAMILY_POOL_ENTRY_SCHEMA,
        "origin_fact_id": fact_id,
        "family_id": project_id_from_path(family_root),
        "source_sibling_id": sibling_id,
        "source_project": project_id_from_path(sibling_root),
        "fact_id": fact_id,
        "verification": ver,
        "claim_summary": claim_summary,
        "artifact": artifact,
        "usable_by": usable_by or [],
        "exported_at": now_iso(),
    }
    # Also write evolve-compatible sightings for unified upsert path
    evolve_row = {
        "origin_fact_id": fact_id,
        "claim_summary": claim_summary,
        "curated_summary": claim_summary,
        "family_relevance": f"Exported from sibling {sibling_id} ({entry['source_project']}).",
        "sightings": [
            {
                "project": entry["source_project"],
                "fact_id": fact_id,
                "status": "verified",
                "verification": ver,
                "assumptions_scope": f"sibling {sibling_id}",
            }
        ],
        "source_sibling_id": sibling_id,
        "usable_by": usable_by or [],
        "artifact": artifact,
        "exported_at": entry["exported_at"],
    }
    upsert_family_entries(family_root, [evolve_row])
    return entry


def build_pool_context_block(
    sibling_root: Path,
    *,
    family_root: Path,
    sibling_id: str,
    limit: int = 12,
) -> str:
    """Prompt block listing family-pool leads applicable to this sibling."""
    entries = applicable_pool_entries(
        family_root,
        sibling_id=sibling_id,
        source_project=project_id_from_path(sibling_root),
        limit=limit,
    )
    rel_pool = _relative_pool_path(sibling_root, family_root)
    lines = [
        "Family closure context:",
        f"- This project is sibling `{sibling_id}` of family `{project_id_from_path(family_root)}`.",
        f"- Shared verified-fact pool: `{rel_pool}` ({len(load_pool(family_root))} total entries).",
        "- Pool entries are verified **in the source sibling's setting only** — import as `reviewed` and "
        "**re-verify locally** before citing in proofs or final assembly.",
        "- Search the pool with `iteris tool memory search . --query ... --json` (hits tagged `scope: family`).",
        "- After promoting a local verified fact for siblings, run "
        f"`iteris family export {family_root} --from {sibling_root} --fact-id <fact:id>`.",
    ]
    if entries:
        lines.append("- Applicable pool entries (newest selection):")
        for row in entries:
            src = row.get("source_sibling_id") or row.get("source_project") or "?"
            fid = row.get("origin_fact_id") or row.get("fact_id") or "?"
            summary = str(row.get("claim_summary") or row.get("curated_summary") or "")[:240]
            lines.append(f"    - [{src}] `{fid}` — {summary}")
    else:
        lines.append("- No pool entries yet tagged for this sibling; export verified facts as they land.")
    lines.append("")
    return "\n".join(lines) + "\n"


def _relative_pool_path(sibling_root: Path, family_root: Path) -> str:
    try:
        return str(pool_index_path(family_root).relative_to(sibling_root.resolve()))
    except ValueError:
        return str(pool_index_path(family_root))

"""Family-level long-term memory, anchored in an evolve root project.

``memory/family/`` is written ONLY by the evolve supervisor (single writer):

- ``FAMILY_INDEX.jsonl``  — curated fact intelligence, one line per origin
  fact, with per-project sightings. Entries are leads, never locally-trusted
  facts: descendants must import-as-``reviewed`` and re-verify before use.
- ``failed_paths.jsonl``  — family-wide dead ends / boundary evidence.
- ``inputs.jsonl``        — union of load-bearing-input vocabulary across
  the family's generalize analyses (the overlap coordinate system).

Descendants locate the root in O(1) via ``evolve_root`` in their own
``.iteris/generalize.json`` (propagated at seed time); memory search merges
family ledgers by default with tagged results.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from iteris.project import append_jsonl, now_iso, read_json

FAMILY_INDEX_SCHEMA = "iteris.family_index_line.v0"
FAMILY_FAILED_PATH_SCHEMA = "iteris.family_failed_path.v0"
FAMILY_INPUT_SCHEMA = "iteris.family_input.v0"

RE_VERIFY_HINT = "family intelligence — re-verify locally before relying on it"


def family_dir(root: Path) -> Path:
    return root / "memory" / "family"


def family_index_path(root: Path) -> Path:
    return family_dir(root) / "FAMILY_INDEX.jsonl"


def failed_paths_path(root: Path) -> Path:
    return family_dir(root) / "failed_paths.jsonl"


def inputs_path(root: Path) -> Path:
    return family_dir(root) / "inputs.jsonl"


def is_family_root(project_root: Path) -> bool:
    root = project_root.resolve()
    if (root / ".iteris" / "FAMILY.json").exists():
        return True
    return family_dir(root).is_dir()


def resolve_family_root(project_root: Path) -> Path | None:
    """The family root whose ledgers this project should read, or None.

    Closure-family siblings carry ``.iteris/family.json`` (written by
    ``iteris family new``). Evolve descendants use ``evolve_root`` in
    ``generalize.json``. A directory with ``.iteris/FAMILY.json`` is itself
    a closure-family root.
    """
    project_root = project_root.resolve()
    marker = read_json(project_root / ".iteris" / "family.json", default=None)
    if isinstance(marker, dict) and marker.get("family_root"):
        candidate = Path(str(marker["family_root"]))
        if candidate.is_dir():
            return candidate.resolve()
    if (project_root / ".iteris" / "FAMILY.json").exists():
        return project_root
    lineage = read_json(project_root / ".iteris" / "generalize.json", default={})
    entry = lineage.get("evolve_root") if isinstance(lineage, dict) else None
    if isinstance(entry, dict) and entry.get("path"):
        candidate = Path(str(entry["path"]))
        if is_family_root(candidate):
            return candidate.resolve()
    if is_family_root(project_root):
        return project_root
    return None


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(row, dict):
            rows.append(row)
    return rows


def load_family_index(root: Path) -> list[dict[str, Any]]:
    return _load_jsonl(family_index_path(root))


def upsert_family_entries(root: Path, entries: list[dict[str, Any]]) -> int:
    """Merge curated entries into FAMILY_INDEX.jsonl by ``origin_fact_id``.

    The ledger is small (one line per origin fact across the family), so the
    file is rewritten atomically as a whole; ``sightings`` lists are merged
    by (project, fact_id) with newer rows replacing older ones.
    """
    merged: dict[str, dict[str, Any]] = {
        str(row.get("origin_fact_id")): row for row in load_family_index(root)
    }
    count = 0
    for entry in entries:
        origin = str(entry.get("origin_fact_id") or "")
        if not origin.startswith("fact:"):
            continue
        count += 1
        incoming = dict(entry)
        incoming["schema_version"] = FAMILY_INDEX_SCHEMA
        incoming["updated_at"] = now_iso()
        existing = merged.get(origin)
        if existing:
            sightings = {
                (s.get("project"), s.get("fact_id")): s
                for s in existing.get("sightings", [])
                if isinstance(s, dict)
            }
            for s in incoming.get("sightings", []) or []:
                if isinstance(s, dict):
                    sightings[(s.get("project"), s.get("fact_id"))] = s
            incoming["sightings"] = sorted(
                sightings.values(), key=lambda s: (str(s.get("project")), str(s.get("fact_id")))
            )
        merged[origin] = incoming
    path = family_index_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for origin in sorted(merged):
            handle.write(json.dumps(merged[origin], ensure_ascii=False, sort_keys=True) + "\n")
    return count


def record_failed_path(root: Path, *, source_project: str, record: dict[str, Any]) -> None:
    append_jsonl(
        failed_paths_path(root),
        {
            "schema_version": FAMILY_FAILED_PATH_SCHEMA,
            "ts": now_iso(),
            "source_project": source_project,
            "record": record,
        },
    )


def update_inputs(root: Path, inputs: list[dict[str, Any]], *, source_project: str) -> int:
    """Union load-bearing inputs into inputs.jsonl, keyed by input ``key``."""
    merged: dict[str, dict[str, Any]] = {
        str(row.get("key")): row for row in _load_jsonl(inputs_path(root))
    }
    count = 0
    for item in inputs:
        key = str(item.get("key") or "").strip()
        if not key:
            continue
        count += 1
        row = dict(item)
        row["schema_version"] = FAMILY_INPUT_SCHEMA
        row["key"] = key
        projects = set(merged.get(key, {}).get("projects") or [])
        projects.add(source_project)
        row["projects"] = sorted(projects)
        row["updated_at"] = now_iso()
        merged[key] = row
    path = inputs_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for key in sorted(merged):
            handle.write(json.dumps(merged[key], ensure_ascii=False, sort_keys=True) + "\n")
    return count


def family_search_rows(project_root: Path) -> list[dict[str, Any]]:
    """Family ledger rows tagged for merge into ordinary memory search."""
    root = resolve_family_root(project_root)
    if root is None:
        return []
    rows: list[dict[str, Any]] = []
    for row in load_family_index(root):
        out = dict(row)
        if str(row.get("schema_version") or "") == "iteris.family_fact.v0":
            out["origin_fact_id"] = row.get("fact_id")
            out.setdefault("claim_summary", row.get("claim_summary") or "")
        out["scope"] = "family"
        out["hint"] = RE_VERIFY_HINT
        rows.append(out)
    for row in _load_jsonl(failed_paths_path(root)):
        out = dict(row)
        out["scope"] = "family"
        out["hint"] = "family dead end — do not re-explore without new information"
        rows.append(out)
    for row in _load_jsonl(inputs_path(root)):
        out = dict(row)
        out["scope"] = "family"
        rows.append(out)
    return rows

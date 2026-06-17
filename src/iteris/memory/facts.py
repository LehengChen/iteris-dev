"""Durable fact validation and indexing."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from iteris.project import now_iso, project_id_from_path

ALLOWED_STATUS = {"draft", "submitted", "reviewed", "verified", "rejected"}
REQUIRED_FIELDS = [
    "fact_id",
    "problem_id",
    "source_project",
    "source_task",
    "predecessors",
    "status",
    "claim_summary",
]
FACT_REF_RE = re.compile(r"fact:[A-Za-z0-9_.:-]*[A-Za-z0-9_-]")


def parse_scalar(value: str) -> Any:
    value = value.strip()
    if value in {"null", "None"}:
        return None
    if value == "[]":
        return []
    if value == "{}":
        return {}
    if value in {"true", "false"}:
        return value == "true"
    if value.startswith('"') and value.endswith('"'):
        return value[1:-1]
    if value.startswith("'") and value.endswith("'"):
        return value[1:-1]
    return value


def parse_frontmatter(text: str) -> tuple[dict[str, Any], str]:
    if not text.startswith("---\n"):
        raise ValueError("missing opening YAML frontmatter marker")
    end = text.find("\n---", 4)
    if end < 0:
        raise ValueError("missing closing YAML frontmatter marker")
    raw = text[4:end].splitlines()
    body = text[end + len("\n---") :]
    data: dict[str, Any] = {}
    i = 0
    while i < len(raw):
        line = raw[i]
        i += 1
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if ":" not in line:
            raise ValueError(f"frontmatter line lacks colon: {line!r}")
        key, value = line.split(":", 1)
        key = key.strip()
        value = value.rstrip()
        if value.strip() == "":
            items: list[Any] = []
            mapping: dict[str, Any] = {}
            while i < len(raw) and (raw[i].startswith(" ") or raw[i].startswith("\t")):
                sub = raw[i].strip()
                i += 1
                if not sub:
                    continue
                if sub.startswith("-"):
                    items.append(parse_scalar(sub[1:].strip()))
                elif ":" in sub:
                    sk, sv = sub.split(":", 1)
                    mapping[sk.strip()] = parse_scalar(sv.strip())
                else:
                    raise ValueError(f"unsupported nested frontmatter line under {key}: {sub!r}")
            data[key] = items if items else mapping
        else:
            data[key] = parse_scalar(value.strip())
    return data, body


def validate_fact_file(path: Path) -> dict[str, Any]:
    errors: list[str] = []
    warnings: list[str] = []
    try:
        meta, body = parse_frontmatter(path.read_text(encoding="utf-8", errors="replace"))
    except Exception as exc:
        return {"path": str(path), "ok": False, "errors": [str(exc)], "warnings": [], "meta": {}}

    for key in REQUIRED_FIELDS:
        if key not in meta:
            errors.append(f"missing required field: {key}")
    if meta.get("status") not in ALLOWED_STATUS:
        errors.append(f"invalid status: {meta.get('status')!r}")
    if not str(meta.get("fact_id", "")).startswith("fact:"):
        errors.append("fact_id must start with fact:")
    predecessors = meta.get("predecessors")
    if not isinstance(predecessors, list):
        errors.append("predecessors must be a list")
    else:
        for pred in predecessors:
            if not isinstance(pred, str) or not pred.startswith("fact:"):
                errors.append(f"invalid predecessor: {pred!r}")
    claim_summary = meta.get("claim_summary")
    if not isinstance(claim_summary, str) or not claim_summary.strip():
        errors.append("claim_summary must be a non-empty string")
    origin = meta.get("origin_fact_id")
    if origin is not None and (not isinstance(origin, str) or not origin.startswith("fact:")):
        errors.append("origin_fact_id must start with fact:")
    if "## statement" not in body:
        errors.append("missing ## statement section")
    cited = sorted(set(FACT_REF_RE.findall(body)))
    if cited and isinstance(predecessors, list):
        missing = [ref for ref in cited if ref not in predecessors and ref != meta.get("fact_id")]
        if missing:
            warnings.append("body cites fact refs not in predecessors: " + ", ".join(missing[:8]))
    if meta.get("status") == "verified" and meta.get("review_level") in (None, "none"):
        warnings.append("status is verified but review_level is none; promote via `iteris tool memory promote-fact` to normalize trust metadata")
    return {"path": str(path), "ok": not errors, "errors": errors, "warnings": warnings, "meta": meta}


def resolve_origin_fact_id(meta: dict[str, Any]) -> str | None:
    """The id minted when this fact was first created, invariant under inheritance.

    Facts without an explicit ``origin_fact_id`` are their own origin (legacy facts
    and freshly minted facts alike), so inheritance chains group correctly without
    any migration.
    """
    origin = meta.get("origin_fact_id")
    if isinstance(origin, str) and origin.startswith("fact:"):
        return origin
    fact_id = meta.get("fact_id")
    return fact_id if isinstance(fact_id, str) else None


def fact_files(project_root: Path) -> list[Path]:
    return sorted((project_root / "memory" / "facts").glob("fact-*.md"))


def find_fact_file(project_root: Path, fact_id: str) -> Path | None:
    for path in fact_files(project_root):
        result = validate_fact_file(path)
        if result.get("meta", {}).get("fact_id") == fact_id:
            return path
    return None


def validate_project_facts(project_root: Path, *, rebuild: bool = True) -> dict[str, Any]:
    results = [validate_fact_file(path) for path in fact_files(project_root)]
    ok = all(row["ok"] for row in results)
    rebuilt = 0
    if ok and rebuild:
        rebuilt = rebuild_fact_index(project_root)
    fact_type_counts: dict[str, int] = {}
    for row in results:
        if not row.get("ok"):
            continue
        fact_type = str((row.get("meta") or {}).get("fact_type") or "unknown")
        fact_type_counts[fact_type] = fact_type_counts.get(fact_type, 0) + 1
    return {
        "ok": ok,
        "count": len(results),
        "rebuilt": rebuilt,
        "fact_type_counts": fact_type_counts,
        "results": results,
    }


def rebuild_fact_index(project_root: Path) -> int:
    rows: list[dict[str, Any]] = []
    updated_at = now_iso()
    for path in fact_files(project_root):
        result = validate_fact_file(path)
        if not result["ok"]:
            continue
        meta = result["meta"]
        rows.append(
            {
                "schema_version": "iteris.fact_index_line.v0",
                "project_id": meta.get("problem_id") or project_id_from_path(project_root),
                "fact_id": meta.get("fact_id"),
                "origin_fact_id": resolve_origin_fact_id(meta),
                "path": str(path.relative_to(project_root)),
                "status": meta.get("status"),
                "fact_type": meta.get("fact_type"),
                "review_level": meta.get("review_level"),
                "claim_policy": meta.get("claim_policy"),
                "claim_summary": meta.get("claim_summary"),
                "source_project": meta.get("source_project"),
                "source_task": meta.get("source_task"),
                "predecessors": meta.get("predecessors") or [],
                "verification": meta.get("verification"),
                "updated_at": updated_at,
            }
        )
    index = project_root / "memory" / "facts" / "FACT_INDEX.jsonl"
    with index.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
    return len(rows)


def _fact_index_rows(project_root: Path) -> list[dict[str, Any]]:
    index_path = project_root / "memory" / "facts" / "FACT_INDEX.jsonl"
    if not index_path.exists():
        return []
    rows: list[dict[str, Any]] = []
    for line in index_path.read_text(encoding="utf-8", errors="replace").splitlines():
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


def fact_in_degrees(
    project_root: Path,
    *,
    rows: list[dict[str, Any]] | None = None,
    include_body_citations: bool = True,
) -> dict[str, int]:
    """In-degree per fact id: how many other facts depend on it.

    Edges come from `predecessors` metadata UNION `fact:` citations in the
    fact body (deduplicated per citing fact). Live runs fill predecessors
    poorly while bodies cite freely, so metadata alone starves keystone
    detection; body citations recover the real dependency graph.
    """
    degrees: dict[str, int] = {}
    for row in rows if rows is not None else _fact_index_rows(project_root):
        fact_id = str(row.get("fact_id") or "")
        if fact_id:
            degrees.setdefault(fact_id, 0)
        targets = {str(pred) for pred in row.get("predecessors") or []}
        if include_body_citations:
            rel = str(row.get("path") or "")
            path = project_root / rel
            if rel and path.exists():
                try:
                    # Scan the body only: frontmatter contains origin_fact_id /
                    # provenance ids that are references, not dependencies.
                    _, body = parse_frontmatter(path.read_text(encoding="utf-8", errors="replace"))
                except ValueError:
                    body = ""
                targets.update(FACT_REF_RE.findall(body))
        targets.discard(fact_id)
        targets.discard("")
        for target in targets:
            degrees[target] = degrees.get(target, 0) + 1
    return degrees


def keystone_facts(
    project_root: Path,
    *,
    min_in_degree: int = 3,
    verification_counts: dict[str, int] | None = None,
) -> list[dict[str, Any]]:
    """Verified facts whose in-degree makes them keystones of the proof tree.

    A keystone with a single verification is a concentration of risk: a flaw
    there collapses everything downstream. ``verification_counts`` (fact id ->
    number of passed verifications) marks which keystones are under-verified.
    """
    rows = _fact_index_rows(project_root)
    degrees = fact_in_degrees(project_root, rows=rows)
    keystones: list[dict[str, Any]] = []
    for row in rows:
        fact_id = str(row.get("fact_id") or "")
        in_degree = degrees.get(fact_id, 0)
        if not fact_id or in_degree < min_in_degree:
            continue
        if row.get("status") not in {"verified", "reviewed"}:
            continue
        passed = (verification_counts or {}).get(fact_id, 1 if row.get("verification") else 0)
        keystones.append(
            {
                "fact_id": fact_id,
                "in_degree": in_degree,
                "status": row.get("status"),
                "claim_summary": row.get("claim_summary"),
                "verification": row.get("verification"),
                "passed_verifications": passed,
                "under_verified": passed < 2,
            }
        )
    keystones.sort(key=lambda item: -item["in_degree"])
    return keystones


def write_fact(
    project_root: Path,
    *,
    fact_id: str,
    source_task: str,
    claim_summary: str,
    statement: str,
    status: str = "submitted",
    fact_type: str = "claim",
    predecessors: list[str] | None = None,
    notes: str = "",
    verification: str | None = None,
    claim_policy: str = "stable_claim",
    review_level: str = "none",
    origin_fact_id: str | None = None,
) -> Path:
    project_id = project_id_from_path(project_root)
    slug = re.sub(r"[^A-Za-z0-9_.-]+", "-", fact_id.replace("fact:", "")).strip("-")
    path = project_root / "memory" / "facts" / f"fact-{slug}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    if predecessors:
        predecessors_block = "predecessors:\n" + "\n".join(f"  - {pred}" for pred in predecessors) + "\n"
    else:
        predecessors_block = "predecessors: []\n"
    verification_line = f"verification: {verification}\n" if verification else "verification: null\n"
    origin_line = f"origin_fact_id: {origin_fact_id}\n" if origin_fact_id else ""
    text = (
        "---\n"
        f"fact_id: {fact_id}\n"
        f"{origin_line}"
        f"problem_id: {project_id}\n"
        f"source_project: {project_id}\n"
        f"source_task: {source_task}\n"
        f"{predecessors_block}"
        f"status: {status}\n"
        f"fact_type: {fact_type}\n"
        f"review_level: {review_level}\n"
        f"claim_policy: {claim_policy}\n"
        f"claim_summary: \"{claim_summary.replace(chr(34), chr(39))}\"\n"
        f"{verification_line}"
        "---\n\n"
        "## statement\n\n"
        f"{statement.strip()}\n\n"
        "## notes\n\n"
        f"{notes.strip() or 'Generated by Iteris bootstrap workflow.'}\n"
    )
    path.write_text(text, encoding="utf-8")
    return path


def update_fact_metadata(
    project_root: Path,
    *,
    fact_id: str,
    status: str,
    verification: str,
    review_level: str | None = None,
) -> Path:
    if status not in ALLOWED_STATUS:
        raise ValueError(f"invalid status: {status}")
    path = find_fact_file(project_root, fact_id)
    if path is None:
        raise FileNotFoundError(f"fact not found: {fact_id}")
    fields = {"status": status, "verification": verification}
    if review_level is not None:
        fields["review_level"] = review_level
    _set_frontmatter_fields(path, fields)
    result = validate_fact_file(path)
    if not result["ok"]:
        raise ValueError("; ".join(result["errors"]))
    return path


def _set_frontmatter_fields(path: Path, fields: dict[str, str]) -> None:
    text = path.read_text(encoding="utf-8", errors="replace")
    if not text.startswith("---\n"):
        raise ValueError("missing opening YAML frontmatter marker")
    end = text.find("\n---", 4)
    if end < 0:
        raise ValueError("missing closing YAML frontmatter marker")
    lines = text[4:end].splitlines()
    body = text[end + len("\n---") :]
    seen: set[str] = set()
    updated: list[str] = []
    for line in lines:
        if ":" not in line or line.startswith((" ", "\t")):
            updated.append(line)
            continue
        key = line.split(":", 1)[0].strip()
        if key in fields:
            updated.append(f"{key}: {fields[key]}")
            seen.add(key)
        else:
            updated.append(line)
    for key, value in fields.items():
        if key not in seen:
            updated.append(f"{key}: {value}")
    path.write_text("---\n" + "\n".join(updated) + "\n---" + body, encoding="utf-8")

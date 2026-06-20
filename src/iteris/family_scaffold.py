"""Scaffold a family closure workspace: N sibling Iteris projects + shared pool."""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

from iteris.commands.new import perform_new_project
from iteris.family import (
    FAMILY_SCHEMA,
    DEFAULT_POLICY,
    DEFAULT_SCHEDULE,
    FamilyError,
    family_path,
    has_family_state,
    write_sibling_marker,
    write_state,
)
from iteris.project import is_project, now_iso, project_id_from_path, session_slug, slugify, write_json
from iteris.references import import_references

FAMILY_MANIFEST_SCHEMA = "iteris.family_manifest.v1"


def load_manifest(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise FamilyError(f"manifest must be a JSON object: {path}")
    return payload


def _ensure_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def _write_watchdog_goal(
    sibling_root: Path,
    *,
    family_root: Path,
    sibling_id: str,
    north_star: str,
    target_artifact: str | None,
    claim_prefix: str | None,
    gaps: list[str],
) -> Path:
    rel_pool = "../memory/family/FAMILY_INDEX.jsonl"
    gap_text = ", ".join(gaps) if gaps else "(none specified)"
    prefix = claim_prefix or "(use family full-closure prefix when assembling)"
    body = (
        f"FAMILY SIBLING {sibling_id} — {project_id_from_path(family_root)}\n\n"
        f"=== NORTH STAR ===\n{north_star.strip()}\n\n"
        f"=== TARGET ===\n{target_artifact or 'results/<problem_id>/answer_northstar_verified.md'}\n\n"
        f"=== CLAIM PREFIX ===\n{prefix}\n\n"
        f"=== GAPS ===\n{gap_text}\n\n"
        f"=== FAMILY SHARED POOL ===\n"
        f"- Read verified leads from `{rel_pool}` (sibling exports only).\n"
        f"- After `iteris tool memory promote-fact`, export with:\n"
        f"  iteris family export {family_root} --from . --fact-id <fact:id>\n"
        f"- Search: `iteris tool memory search . --query ... --json` (scope: family).\n"
        f"- Re-verify every pool fact locally before assembly.\n\n"
        f"=== SHARED REFERENCES ===\n"
        f"- Family shared refs: `references/family_shared/`\n"
    )
    path = sibling_root / ".iteris" / "watchdog_goal.txt"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body + "\n", encoding="utf-8")
    return path


def _link_shared_refs(family_root: Path, sibling_root: Path) -> None:
    shared = family_root / "references"
    dest = sibling_root / "references" / "family_shared"
    if not shared.is_dir():
        return
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists() or dest.is_symlink():
        return
    try:
        dest.symlink_to(Path("..") / ".." / "references", target_is_directory=True)
    except OSError:
        readme = dest.parent / "FAMILY_SHARED_README.md"
        readme.write_text(
            f"Shared family references live at `{shared.relative_to(sibling_root)}`.\n",
            encoding="utf-8",
        )


def create_family(
    family_root: Path,
    *,
    goal: str,
    siblings: list[dict[str, Any]],
    schedule: dict[str, Any] | None = None,
    policy: dict[str, Any] | None = None,
    shared_references: list[Path] | None = None,
    source_problems_doc: str | None = None,
) -> dict[str, Any]:
    """Create a full family workspace with N sibling Iteris projects."""
    family_root = family_root.resolve()
    if has_family_state(family_root):
        raise FamilyError(f"family already exists: {family_path(family_root)}")
    if not siblings:
        raise FamilyError("at least one sibling required")

    _ensure_dir(family_root / ".iteris")
    _ensure_dir(family_root / "docs")
    _ensure_dir(family_root / "references")
    _ensure_dir(family_root / "memory" / "family")
    _ensure_dir(family_root / "results")

    if shared_references:
        import_references(family_root, list(shared_references))

    problems_doc = source_problems_doc or goal
    (family_root / "docs" / "SOURCE_PROBLEMS.md").write_text(
        f"# Family source problems\n\n{problems_doc.strip()}\n",
        encoding="utf-8",
    )
    (family_root / "docs" / "FAMILY_OPERATOR.md").write_text(
        _operator_stub(goal),
        encoding="utf-8",
    )

    sibling_records: list[dict[str, Any]] = []
    for spec in siblings:
        record = _scaffold_sibling(family_root, spec)
        sibling_records.append(record)

    state = {
        "schema_version": FAMILY_SCHEMA,
        "goal": goal.strip(),
        "created_at": now_iso(),
        "schedule": {**DEFAULT_SCHEDULE, **(schedule or {})},
        "policy": {**DEFAULT_POLICY, **(policy or {})},
        "run": {"started_at": None},
        "siblings": sibling_records,
        "pool": {
            "index": "memory/family/FAMILY_INDEX.jsonl",
            "export_command": "iteris family export",
        },
    }
    write_state(family_root, state)
    for entry in sibling_records:
        sibling_root = family_root / entry["path"]
        write_sibling_marker(sibling_root, family_root, entry["sibling_id"])
    return state


def _scaffold_sibling(family_root: Path, spec: dict[str, Any]) -> dict[str, Any]:
    sibling_id = str(spec.get("sibling_id") or spec.get("id") or "").strip()
    if not sibling_id:
        raise FamilyError("each sibling needs sibling_id")

    dir_name = str(spec.get("path") or spec.get("dir") or slugify(sibling_id, 50)).strip()
    sibling_root = (family_root / dir_name).resolve()
    if sibling_root.exists() and is_project(sibling_root):
        raise FamilyError(f"sibling path already an Iteris project: {sibling_root}")

    source_path = spec.get("source")
    if not source_path:
        raise FamilyError(f"sibling {sibling_id} requires source (path to problem statement file)")
    source = Path(str(source_path))
    if not source.is_absolute():
        source = (family_root / source).resolve()
    if not source.exists():
        raise FamilyError(f"source not found for {sibling_id}: {source}")

    sibling_refs = [Path(p) for p in (spec.get("references") or [])]
    for i, ref in enumerate(sibling_refs):
        if not ref.is_absolute():
            sibling_refs[i] = (family_root / ref).resolve()

    perform_new_project(
        sibling_root,
        source=source,
        references=sibling_refs or None,
        allow_non_empty=False,
    )

    title = str(spec.get("title") or dir_name)
    toml = sibling_root / "iteris.toml"
    if toml.exists():
        text = toml.read_text(encoding="utf-8")
        if 'title = "' in text:
            import re

            text = re.sub(r'^title\s*=.*$', f'title = "{title}"', text, count=1, flags=re.MULTILINE)
            toml.write_text(text, encoding="utf-8")

    north_star = str(spec.get("north_star") or spec.get("goal") or title).strip()
    north_star_file = spec.get("north_star_file")
    if north_star_file:
        ns_path = Path(str(north_star_file))
        if not ns_path.is_absolute():
            ns_path = (family_root / ns_path).resolve()
        north_star = ns_path.read_text(encoding="utf-8").strip()

    target = spec.get("target_artifact")
    if not target:
        pid = slugify(dir_name, 50)
        target = f"results/{pid}/answer_northstar_verified.md"

    gaps = spec.get("gaps") or []
    if isinstance(gaps, str):
        gaps = [g.strip() for g in gaps.replace("|", ",").split(",") if g.strip()]

    session = spec.get("session") or f"iteris-{session_slug(dir_name)}"
    claim_prefix = spec.get("claim_prefix")

    _write_watchdog_goal(
        sibling_root,
        family_root=family_root,
        sibling_id=sibling_id,
        north_star=north_star,
        target_artifact=str(target),
        claim_prefix=str(claim_prefix) if claim_prefix else None,
        gaps=gaps,
    )
    _link_shared_refs(family_root, sibling_root)

    try:
        src_rel = str(source.relative_to(family_root.resolve()))
    except ValueError:
        src_rel = str(source)
    return {
        "sibling_id": sibling_id,
        "path": dir_name,
        "title": title,
        "session": session,
        "target_artifact": str(target),
        "gaps": gaps,
        "claim_prefix": claim_prefix,
        "north_star": north_star[:500],
        "source": src_rel,
    }


def _operator_stub(goal: str) -> str:
    return (
        "# Family operator notes\n\n"
        f"## Goal\n\n{goal.strip()}\n\n"
        "## Commands\n\n"
        "```bash\n"
        "iteris family status\n"
        "iteris family schedule --dry-run\n"
        "iteris family start\n"
        "iteris family export . --from <sibling-path> --fact-id <fact:id>\n"
        "```\n\n"
        "## Shared pool\n\n"
        "Verified facts export to `memory/family/FAMILY_INDEX.jsonl`. "
        "Siblings search via `iteris tool memory search` and must re-verify before cite.\n"
    )


def manifest_to_create_args(manifest: dict[str, Any]) -> dict[str, Any]:
    goal = str(manifest.get("goal") or manifest.get("family_goal") or "").strip()
    if not goal:
        raise FamilyError("manifest requires goal")
    siblings = manifest.get("siblings") or []
    if not isinstance(siblings, list):
        raise FamilyError("manifest siblings must be a list")
    schedule = manifest.get("schedule") if isinstance(manifest.get("schedule"), dict) else {}
    policy = manifest.get("policy") if isinstance(manifest.get("policy"), dict) else {}
    shared = [Path(p) for p in (manifest.get("shared_references") or [])]
    return {
        "goal": goal,
        "siblings": siblings,
        "schedule": schedule,
        "policy": policy,
        "shared_references": shared or None,
        "source_problems_doc": manifest.get("source_problems_doc"),
    }

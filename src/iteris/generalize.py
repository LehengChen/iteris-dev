"""Seed a sibling Iteris project from a parent project's verified conclusion.

A *generalization* is structurally a special ``new``: instead of starting from a
raw source problem, it starts from a parent project's verified result plus a
generalization direction, inheriting the transferable verified facts as a trusted
(but re-checkable) starting point. This module holds the pure seeding logic; the
terminal interaction and CLI output live in ``iteris.commands.generalize``.
"""

from __future__ import annotations

import json
import re
import shutil
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from iteris.commands.goal import (
    build_goal_prompt,
    build_project_context_lines,
    project_target_artifact,
    resolve_goal_defaults,
    verified_artifact_for,
)
from iteris.gitops import GitError, checkpoint, init_git
from iteris.memory.facts import (
    fact_files,
    parse_frontmatter,
    rebuild_fact_index,
    resolve_origin_fact_id,
    validate_fact_file,
    write_fact,
)
from iteris.memory.scratch import append as scratch_append
from iteris.project import (
    init_project,
    is_project,
    now_iso,
    project_id_from_path,
    require_project,
    slugify,
    source_file,
    write_json,
)

LINEAGE_SCHEMA = "iteris.generalize_lineage.v0"
INHERITED_SOURCE_TASK = "task-generalization-seed"


class GeneralizeError(RuntimeError):
    """Raised for user-facing generalization seeding errors."""


@dataclass
class DirectionSpec:
    """A resolved generalization direction: either a parent file or free text."""

    kind: str  # "file" | "text"
    title: str
    slug: str
    seed_filename: str
    body_markdown: str
    origin: str | None = None  # parent-relative path when kind == "file"


@dataclass
class SeedResult:
    """Outcome of seeding a generalization project."""

    child_root: Path
    parent_root: Path
    problem_id: str
    target_artifact: str
    goal: str
    direction: DirectionSpec
    source_result_rel: str
    inherited: list[dict[str, Any]] = field(default_factory=list)
    lineage_path: Path | None = None
    git: dict[str, Any] = field(default_factory=dict)


# --------------------------------------------------------------------------- #
# Parent + source resolution
# --------------------------------------------------------------------------- #


def resolve_parent(parent_path: str | Path) -> Path:
    """Resolve and validate the parent Iteris project root."""
    return require_project(parent_path)


def resolve_source_result(parent_root: Path, source_result: str | None) -> str:
    """Return the parent-relative path of the verified result to generalize.

    When omitted, default to the parent's *verified* answer
    (``answer_verified.md``), which finalize() writes only after goal-success
    verification passes — generalizing must start from a verified result, not
    the working ``answer.md`` that exists mid-run regardless of verdict. Fall
    back to the recorded target only if no verified copy exists, so an explicit
    --source-result or a legacy answer_verified.md target still resolves.
    Raises ``GeneralizeError`` when the resolved file does not exist.
    """
    if source_result:
        rel = source_result
    else:
        recorded = project_target_artifact(parent_root)
        rel = None
        if recorded:
            verified = verified_artifact_for(recorded)
            rel = verified if (parent_root / verified).is_file() else recorded
    if not rel:
        raise GeneralizeError(
            "could not determine a source result; pass --source-result <path relative to parent>"
        )
    candidate = (parent_root / rel).resolve()
    if not candidate.is_file():
        raise GeneralizeError(f"source result not found in parent project: {rel}")
    return rel


# --------------------------------------------------------------------------- #
# Direction handling
# --------------------------------------------------------------------------- #


def _first_heading(text: str) -> str | None:
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("# "):
            return stripped[2:].strip()
    return None


def classify_direction(parent_root: Path, direction: str) -> DirectionSpec:
    """Classify ``--direction`` as a markdown file or free text and materialize it.

    A file direction is copied verbatim into the child ``sources/`` with an origin
    banner prepended (its relative links point back at the parent and would be
    stale otherwise). A text direction is wrapped in a generated seed document.
    """
    file_candidate: Path | None = None
    # Only probe the filesystem when the string could plausibly be a path:
    # free-text directions (multi-line, or with a >NAME_MAX component) make
    # stat() raise ENAMETOOLONG, which pathlib does not swallow.
    looks_like_path = "\n" not in direction and len(direction) < 1024
    if looks_like_path:
        try:
            abs_candidate = Path(direction).expanduser()
            parent_candidate = parent_root / direction
            if abs_candidate.is_file():
                file_candidate = abs_candidate.resolve()
            elif parent_candidate.is_file():
                file_candidate = parent_candidate.resolve()
        except OSError:
            file_candidate = None

    if file_candidate is not None:
        raw = file_candidate.read_text(encoding="utf-8", errors="replace")
        title = _first_heading(raw) or file_candidate.stem
        try:
            origin = str(file_candidate.relative_to(parent_root.resolve()))
        except ValueError:
            origin = str(file_candidate)
        banner = (
            f"> Generalization direction copied from `{origin}` in parent project "
            f"`{parent_root.name}`. Relative links in this document point at the parent "
            f"project, not this one.\n\n"
        )
        return DirectionSpec(
            kind="file",
            title=title,
            slug=slugify(title, 40) or "direction",
            seed_filename=file_candidate.name,
            body_markdown=banner + raw,
            origin=origin,
        )

    # Free-text direction.
    text = direction.strip()
    title = text.splitlines()[0].strip() if text else "generalization-direction"
    title = title[:120]
    body = (
        f"# {title}\n\n"
        "## Generalization direction\n\n"
        f"{text}\n\n"
        "## Notes\n\n"
        "This direction was provided as free text to `iteris generalize`. State the precise "
        "generalized theorem to prove, then pursue it using the inherited facts (which start as "
        "`reviewed` and must be re-verified for applicability).\n"
    )
    return DirectionSpec(
        kind="text",
        title=title,
        slug=slugify(title, 40) or "direction",
        seed_filename="generalization-direction.md",
        body_markdown=body,
        origin=None,
    )


def default_target_dir(parent_root: Path, spec: DirectionSpec) -> Path:
    """Default sibling directory: ``<parent-name>-gen-<direction-slug>``."""
    return parent_root.parent / f"{parent_root.name}-gen-{spec.slug}"


# --------------------------------------------------------------------------- #
# Success contract
# --------------------------------------------------------------------------- #

_CONTRACT_SECTIONS = (
    ("success_criteria", "## Success criteria"),
    ("does_not_count", "## What does NOT count"),
    ("audit_routes", "## Audit routes"),
    ("experiment_gate", "## Experiment gate"),
)


def append_contract_sections(body_markdown: str, contract: dict[str, Any]) -> str:
    """Render structured contract fields into the direction document itself.

    The goal-success verifier reads ``sources/<direction>.md``, not the pool
    entry — criteria that live only in EVOLVE.json are invisible to it.
    Sections whose heading already appears in the document are left to the
    author's wording and not duplicated."""
    lower = body_markdown.lower()
    parts: list[str] = []
    for key, heading in _CONTRACT_SECTIONS:
        value = contract.get(key)
        if not value or heading.lower() in lower:
            continue
        parts.append(heading)
        parts.append("")
        if key == "experiment_gate":
            parts.append(str(value).strip())
        elif key == "audit_routes":
            for item in value:
                route = item.get("route") if isinstance(item, dict) else item
                hint = item.get("hint") if isinstance(item, dict) else None
                parts.append(f"- {route}" + (f" — {hint}" if hint else ""))
            parts.append("")
            parts.append(
                "Every route above must receive an explicit verdict — a proof or a "
                "precisely-witnessed obstruction — followed by a synthesis verdict. "
                "Do not close on the first positive result; re-proving already-known "
                "results does not count."
            )
        else:
            parts.extend(f"- {item}" for item in value)
        parts.append("")
    if not parts:
        return body_markdown
    return body_markdown.rstrip() + "\n\n" + "\n".join(parts).rstrip() + "\n"


# --------------------------------------------------------------------------- #
# Fact discovery + inheritance
# --------------------------------------------------------------------------- #


def _extract_statement(body: str) -> str:
    """Pull the ``## statement`` section text out of a fact body."""
    marker = "## statement"
    idx = body.find(marker)
    if idx < 0:
        return body.strip()
    rest = body[idx + len(marker):]
    next_heading = re.search(r"\n##\s", rest)
    section = rest[: next_heading.start()] if next_heading else rest
    return section.strip()


def read_curated_fact_ids(parent_root: Path) -> list[str]:
    """Return the parent's curated core fact ids from STATUS.md ``verified_facts:``.

    This block is the human/agent-curated chain that actually composes the terminal
    result. It is a far more reliable "what is transferable" signal than the per-fact
    ``claim_policy`` (which in practice tags even core lemmas as ``planning_hint``).
    Returns an empty list when the block is absent.
    """
    status_path = parent_root / "STATUS.md"
    if not status_path.exists():
        return []
    ids: list[str] = []
    in_block = False
    for line in status_path.read_text(encoding="utf-8", errors="replace").splitlines():
        if not in_block:
            if line.strip() == "verified_facts:":
                in_block = True
            continue
        # Inside the block: indented "- fact:..." items; any unindented line ends it.
        if line[:1] not in (" ", "\t"):
            break
        item = line.strip()
        if item.startswith("- "):
            value = item[2:].strip()
            if value.startswith("fact:"):
                ids.append(value)
    return ids


def discover_verified_facts(parent_root: Path) -> list[dict[str, Any]]:
    """Return the parent project's ``status: verified`` facts as plain dicts."""
    facts: list[dict[str, Any]] = []
    for path in fact_files(parent_root):
        result = validate_fact_file(path)
        if not result["ok"]:
            continue
        meta = result["meta"]
        if meta.get("status") != "verified":
            continue
        try:
            _, body = parse_frontmatter(path.read_text(encoding="utf-8", errors="replace"))
        except Exception:
            body = ""
        facts.append(
            {
                "fact_id": meta.get("fact_id"),
                "origin_fact_id": resolve_origin_fact_id(meta),
                "claim_summary": meta.get("claim_summary", ""),
                "fact_type": meta.get("fact_type"),
                "claim_policy": meta.get("claim_policy"),
                "verification": meta.get("verification"),
                "path": str(path.relative_to(parent_root)),
                "statement": _extract_statement(body),
            }
        )
    facts.sort(key=lambda item: str(item.get("fact_id")))
    return facts


def default_fact_selection(parent_root: Path, discovered: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Pick the default facts to inherit: the curated core chain when available.

    Falls back to all discovered verified facts when the parent has no curated
    ``verified_facts:`` block in STATUS.md.
    """
    curated = read_curated_fact_ids(parent_root)
    if not curated:
        return discovered
    wanted = set(curated)
    selected = [fact for fact in discovered if fact.get("fact_id") in wanted]
    return selected or discovered


def _child_fact_id(child_id: str, parent_fact_id: str) -> str:
    parts = parent_fact_id.split(":")
    local = ":".join(parts[2:]) if len(parts) > 2 else parts[-1]
    return f"fact:{child_id}:inherited:{local}"


def import_facts(
    child_root: Path,
    parent_root: Path,
    selected: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Import selected parent facts into the child as ``reviewed`` facts.

    Each imported fact keeps the parent's statement and claim summary, links the
    parent fact via ``predecessors``, and records its origin in the notes. The
    fact index is rebuilt once at the end.
    """
    child_id = project_id_from_path(child_root)
    parent_name = parent_root.name
    imported: list[dict[str, Any]] = []
    for fact in selected:
        parent_fact_id = str(fact["fact_id"])
        child_fact_id = _child_fact_id(child_id, parent_fact_id)
        origin_fact_id = str(fact.get("origin_fact_id") or parent_fact_id)
        notes = (
            f"Inherited from `{parent_name}` fact `{parent_fact_id}` "
            f"(was status: verified, verification {fact.get('verification') or 'n/a'}). "
            "Re-verify applicability in the generalized setting before relying on it."
        )
        path = write_fact(
            child_root,
            fact_id=child_fact_id,
            source_task=INHERITED_SOURCE_TASK,
            claim_summary=fact.get("claim_summary") or "inherited claim",
            statement=fact.get("statement") or "(statement inherited from parent fact)",
            status="reviewed",
            fact_type=fact.get("fact_type") or "inherited",
            predecessors=[parent_fact_id],
            notes=notes,
            claim_policy="inherited_from_parent",
            review_level="reviewed",
            origin_fact_id=origin_fact_id,
        )
        imported.append(
            {
                "child_fact_id": child_fact_id,
                "parent_fact_id": parent_fact_id,
                "origin_fact_id": origin_fact_id,
                "claim_summary": fact.get("claim_summary") or "",
                "path": str(path.relative_to(child_root)),
            }
        )
    rebuild_fact_index(child_root)
    return imported


# --------------------------------------------------------------------------- #
# Rendering
# --------------------------------------------------------------------------- #


def build_generalization_goal(
    spec: DirectionSpec,
    *,
    parent_name: str,
    source_result_rel: str,
    contract: dict[str, Any] | None = None,
) -> str:
    """The plain goal string (domain text) handed to ``build_goal_prompt``."""
    goal = (
        f"Generalize the verified result in `{parent_name}/{source_result_rel}` along the "
        f"direction '{spec.title}'. Prove the generalized theorem stated in "
        f"`sources/{spec.seed_filename}` end-to-end. Inherited facts start as `reviewed` and "
        "must be re-verified for applicability in the generalized setting; produce the verified "
        "target artifact only after fact, assembly, and goal-success verification pass."
    )
    if not contract:
        return goal
    extras: list[str] = []
    if contract.get("success_criteria") or contract.get("does_not_count"):
        extras.append(
            f"Success is judged against the success-contract sections of "
            f"`sources/{spec.seed_filename}`: every item under '## Success criteria' must be "
            "satisfied, and results listed under '## What does NOT count' do not satisfy this "
            "goal even if verified."
        )
    if contract.get("audit_routes"):
        extras.append(
            "This is an AUDIT contract: every route under '## Audit routes' must receive an "
            "explicit verdict (a proof or a precisely-witnessed obstruction) plus a final "
            "synthesis verdict before the goal can close; the first positive result alone "
            "does not complete the goal."
        )
    if contract.get("experiment_gate"):
        extras.append(
            "EXPERIMENT GATE: run the experiment under '## Experiment gate' FIRST, archiving "
            "script, data, manifest, and an explicit scope statement; use its outcome to "
            "allocate effort between proof and refutation."
        )
    extras.append(
        "Label every theorem-level claim in the target artifact with a substance grade: "
        "[NEW] (genuinely new mathematics), [STD] (standard technique instantiated in the "
        "family interface), or [MAP] (problem cartography: reformulation, obstruction "
        "bookkeeping, or making a known difficulty explicit)."
    )
    return goal + " " + " ".join(extras)


def render_project_md(
    child_root: Path,
    *,
    parent_root: Path,
    source_result_rel: str,
    spec: DirectionSpec,
    imported: list[dict[str, Any]],
) -> str:
    lines = [
        f"# {child_root.name}",
        "",
        "Generalization project seeded by `iteris generalize`.",
        "",
        "## Generalized from",
        "",
        f"- Parent project: `{parent_root.name}` (`{parent_root}`)",
        f"- Parent result: `{source_result_rel}`",
        f"- Direction: {spec.title} (see `sources/{spec.seed_filename}`)",
        "",
        "## Inherited facts",
        "",
    ]
    if imported:
        lines.append(
            "These facts were verified in the parent's setting and imported here as "
            "`reviewed`. Re-verify each before relying on it in the generalized proof:"
        )
        lines.append("")
        for item in imported:
            lines.append(
                f"- `{item['child_fact_id']}` — {item['claim_summary']} "
                f"(from `{item['parent_fact_id']}`)"
            )
    else:
        lines.append("No facts were inherited.")
    lines.extend(
        [
            "",
            "Full lineage is recorded in `.iteris/generalize.json`.",
            "",
        ]
    )
    return "\n".join(lines)


def render_roadmap_md(spec: DirectionSpec, imported: list[dict[str, Any]]) -> str:
    inherited_note = (
        f"- Re-verify the {len(imported)} inherited fact(s); they are `reviewed`, not `verified`, "
        "in this project."
        if imported
        else "- Establish the needed lemmas; no facts were inherited."
    )
    return "\n".join(
        [
            "# Roadmap",
            "",
            f"- State the precise generalized theorem for direction: {spec.title}.",
            inherited_note,
            "- Identify which inherited facts transfer unchanged and which need new proofs.",
            "- Prove the generalized theorem and assemble the verified terminal artifact.",
            "",
        ]
    )


def _write_status(
    child_root: Path,
    *,
    target_artifact: str,
    parent_name: str,
    source_result_rel: str,
) -> None:
    src = source_file(child_root)
    lines = [
        "phase: seeded",
        f"last_updated: {now_iso()}",
        f"source: {src.relative_to(child_root) if src else ''}",
        f"target_artifact: {target_artifact}",
        f"generalized_from: {parent_name}/{source_result_rel}",
        "next: iteris run",
        "",
    ]
    (child_root / "STATUS.md").write_text("\n".join(lines), encoding="utf-8")


def read_evolve_root(project_root: Path) -> dict[str, Any] | None:
    """Return the project's ``evolve_root`` lineage entry, if any.

    Present when the project was seeded under an evolve family (directly or via
    an ancestor); points at the root project that owns ``memory/family/`` and
    ``generalize/EVOLVE.json``.
    """
    path = project_root / ".iteris" / "generalize.json"
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except Exception:
        return None
    entry = data.get("evolve_root")
    if isinstance(entry, dict) and entry.get("path"):
        return entry
    return None


def write_lineage(
    child_root: Path,
    *,
    parent_root: Path,
    source_result_rel: str,
    spec: DirectionSpec,
    imported: list[dict[str, Any]],
    goal: str,
    target_artifact: str,
    evolve_root: dict[str, Any] | None = None,
    family_context: str | None = None,
) -> Path:
    path = child_root / ".iteris" / "generalize.json"
    payload: dict[str, Any] = {
        "schema_version": LINEAGE_SCHEMA,
        "created_at": now_iso(),
        "parent_project": {
            "path": str(parent_root),
            "id": project_id_from_path(parent_root),
            "name": parent_root.name,
        },
        "source_result": source_result_rel,
        "direction": {
            "kind": spec.kind,
            "title": spec.title,
            "origin": spec.origin,
            "sources_file": spec.seed_filename,
        },
        "inherited_facts": [
            {
                "child_fact_id": item["child_fact_id"],
                "parent_fact_id": item["parent_fact_id"],
                "origin_fact_id": item.get("origin_fact_id"),
                "claim_summary": item["claim_summary"],
            }
            for item in imported
        ],
        "goal": goal,
        "target_artifact": target_artifact,
    }
    if evolve_root:
        payload["evolve_root"] = evolve_root
    if family_context:
        payload["family_context"] = family_context
    write_json(path, payload)
    return path


def load_lineage(root: Path) -> dict[str, Any] | None:
    """Read .iteris/generalize.json, or None when absent/unreadable."""
    path = root / ".iteris" / "generalize.json"
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def generalization_prompt_context(root: Path) -> dict[str, Any] | None:
    """Rebuild the build_goal_prompt generalization context from the lineage
    snapshot, so prompt rewrites (e.g. `iteris run`) preserve the
    generalization block instead of dropping it."""
    lineage = load_lineage(root)
    if not lineage:
        return None
    direction = lineage.get("direction") or {}
    context: dict[str, Any] = {
        "parent_name": (lineage.get("parent_project") or {}).get("name"),
        "source_result": lineage.get("source_result"),
        "direction_title": direction.get("title"),
        "direction_sources_file": direction.get("sources_file"),
        "inherited_facts": [
            {
                "child_fact_id": item.get("child_fact_id"),
                "claim_summary": item.get("claim_summary", ""),
            }
            for item in lineage.get("inherited_facts") or []
        ],
        "goal": lineage.get("goal"),
    }
    if lineage.get("evolve_root"):
        context["evolve_root"] = lineage["evolve_root"]
    if lineage.get("family_context"):
        context["family_context"] = lineage["family_context"]
    return context


# --------------------------------------------------------------------------- #
# Orchestration
# --------------------------------------------------------------------------- #


def seed_generalization(
    parent_path: str | Path,
    *,
    source_result: str | None,
    direction: str,
    target: str | Path | None = None,
    selected_fact_ids: list[str] | None = None,
    evolve_root: dict[str, Any] | None = None,
    family_context: str | None = None,
    contract: dict[str, Any] | None = None,
) -> SeedResult:
    """Create and seed a sibling generalization project. Does not launch ``run``.

    ``selected_fact_ids`` filters which discovered verified facts to inherit. When
    ``None``, the default is the parent's curated core chain (STATUS.md
    ``verified_facts:``), falling back to all verified facts if that block is absent.
    Bootstrap (``run_once``) is intentionally skipped: a generalization project's
    starting facts are the inherited ones, not a fresh source-problem intake.

    ``evolve_root`` marks the child as belonging to an evolve family. When omitted
    it is propagated verbatim from the parent's own lineage, so grandchildren keep
    an O(1) pointer to the family root no matter how deep the tree grows.
    """
    parent_root = resolve_parent(parent_path)
    if evolve_root is None:
        evolve_root = read_evolve_root(parent_root)
    source_result_rel = resolve_source_result(parent_root, source_result)
    spec = classify_direction(parent_root, direction)
    if contract:
        spec.body_markdown = append_contract_sections(spec.body_markdown, contract)

    child_root = Path(target).resolve() if target else default_target_dir(parent_root, spec)
    if is_project(child_root):
        raise GeneralizeError(
            f"target is already an Iteris project: {child_root}. Pass --target <new dir>."
        )
    if child_root.exists() and any(child_root.iterdir()):
        raise GeneralizeError(
            f"target directory is not empty: {child_root}. Pass --target <new dir>."
        )

    # Materialize the direction into a temp seed file, then let init_project copy it
    # into sources/ and point iteris.toml's source_file at it.
    tmp_dir = Path(tempfile.mkdtemp(prefix="iteris-generalize-"))
    try:
        seed_path = tmp_dir / spec.seed_filename
        seed_path.write_text(spec.body_markdown, encoding="utf-8")
        init_project(child_root, source=seed_path)
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)

    problem_id, target_artifact = resolve_goal_defaults(child_root)
    (child_root / target_artifact).parent.mkdir(parents=True, exist_ok=True)

    discovered = discover_verified_facts(parent_root)
    if selected_fact_ids is not None:
        wanted = set(selected_fact_ids)
        selected = [fact for fact in discovered if fact.get("fact_id") in wanted]
    else:
        selected = default_fact_selection(parent_root, discovered)
    imported = import_facts(child_root, parent_root, selected)

    goal = build_generalization_goal(
        spec,
        parent_name=parent_root.name,
        source_result_rel=source_result_rel,
        contract=contract,
    )

    # Overwrite the defaults init_project wrote.
    (child_root / "PROJECT.md").write_text(
        render_project_md(
            child_root,
            parent_root=parent_root,
            source_result_rel=source_result_rel,
            spec=spec,
            imported=imported,
        ),
        encoding="utf-8",
    )
    (child_root / "ROADMAP.md").write_text(render_roadmap_md(spec, imported), encoding="utf-8")
    _write_status(
        child_root,
        target_artifact=target_artifact,
        parent_name=parent_root.name,
        source_result_rel=source_result_rel,
    )

    lineage_path = write_lineage(
        child_root,
        parent_root=parent_root,
        source_result_rel=source_result_rel,
        spec=spec,
        imported=imported,
        goal=goal,
        target_artifact=target_artifact,
        evolve_root=evolve_root,
        family_context=family_context,
    )

    # Write a run-ready goal prompt with the generalization context block.
    generalization_context = {
        "parent_name": parent_root.name,
        "source_result": source_result_rel,
        "direction_title": spec.title,
        "direction_sources_file": spec.seed_filename,
        "inherited_facts": [
            {"child_fact_id": item["child_fact_id"], "claim_summary": item["claim_summary"]}
            for item in imported
        ],
    }
    if evolve_root:
        generalization_context["evolve_root"] = evolve_root
    if family_context:
        generalization_context["family_context"] = family_context
    prompt = build_goal_prompt(
        goal,
        target_artifact=target_artifact,
        problem_id=problem_id,
        generalization=generalization_context,
        project_context_lines=build_project_context_lines(child_root),
    )
    prompt_path = child_root / ".iteris" / "goal_prompt.txt"
    prompt_path.parent.mkdir(parents=True, exist_ok=True)
    prompt_path.write_text(prompt + "\n", encoding="utf-8")

    scratch_append(
        child_root,
        "decisions",
        {
            "event_type": "generalization_seeded",
            "parent_project": str(parent_root),
            "source_result": source_result_rel,
            "direction_title": spec.title,
            "inherited_fact_count": len(imported),
        },
    )

    git_result: dict[str, Any] = {}
    try:
        init_git(child_root)
        git_result = checkpoint(child_root, message="checkpoint: seed generalization project")
    except GitError as exc:
        git_result = {"committed": False, "reason": str(exc)}

    try:
        from iteris.guide.index import refresh_project_index

        refresh_project_index(child_root)
        refresh_project_index(parent_root)
    except Exception:
        pass

    return SeedResult(
        child_root=child_root,
        parent_root=parent_root,
        problem_id=problem_id,
        target_artifact=target_artifact,
        goal=goal,
        direction=spec,
        source_result_rel=source_result_rel,
        inherited=imported,
        lineage_path=lineage_path,
        git=git_result,
    )

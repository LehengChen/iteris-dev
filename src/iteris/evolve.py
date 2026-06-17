"""Evolve state: the family's direction pool, nodes, budget, and boundary.

``generalize/EVOLVE.json`` in the root project is the single source of truth
for "what to do next" at family level; ``memory/family/`` is "what we know".
Both are written only by the evolve supervisor (single writer). Vocabulary:
the family-level concept is the **direction pool** — "frontier" is reserved
for project-internal semantics and never used here.

Direction lifecycle:
``proposed -> approved -> seeded -> running -> verified | failed | blocked |
superseded | vetoed``. ``approved`` happens mechanically when the veto window
lapses; ``vetoed`` is only ever set by the human via ``iteris evolve veto``.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from iteris.generalize_analyze import CONTRACT_FIELDS
from iteris.project import now_iso, project_id_from_path, read_json, session_slug, slugify, write_json

EVOLVE_SCHEMA = "iteris.evolve_state.v0"

DIRECTION_STATUSES = {
    "proposed",
    "approved",
    "seeded",
    "running",
    "verified",
    # A child that terminated via a certified principled_stop (the run is
    # honestly unreachable-as-stated / reduced to a named obstruction): a
    # first-class terminal, distinct from a full goal_success ``verified`` so
    # downstream tools never treat it as a solved result — but it MUST free the
    # concurrency slot exactly like ``verified`` does.
    "reduced",
    "failed",
    "blocked",
    "superseded",
    "vetoed",
}
TERMINAL_DIRECTION_STATUSES = {"verified", "reduced", "failed", "superseded", "vetoed"}

DEFAULT_BUDGET = {
    "wall_hours": 72.0,
    "max_concurrent": 2,
    "node_stall_hours": 18.0,
    "spent_hours": 0.0,
    "max_nodes": 12,
}
DEFAULT_POLICY = {
    "abstract_bias": True,
    "analysis_directions_per_node": 3,
    "instantiate_quota_per_layer": 1,
    "seed_veto_window_minutes": 60,
}


class EvolveError(RuntimeError):
    """Raised for user-facing evolve state errors."""


def evolve_path(root: Path) -> Path:
    return root / "generalize" / "EVOLVE.json"


def has_evolve_state(root: Path) -> bool:
    return evolve_path(root).exists()


def read_state(root: Path) -> dict[str, Any]:
    state = read_json(evolve_path(root), default=None)
    if not isinstance(state, dict):
        raise EvolveError(f"no evolve state at {evolve_path(root)}; run `iteris evolve init` first")
    return state


def write_state(root: Path, state: dict[str, Any]) -> Path:
    state["updated_at"] = now_iso()
    path = evolve_path(root)
    write_json(path, state)
    return path


def evolve_root_entry(root: Path) -> dict[str, Any]:
    """The ``evolve_root`` lineage entry seeded children carry."""
    return {"path": str(root.resolve()), "node_id": project_id_from_path(root)}


def init_state(
    root: Path,
    *,
    goal: str,
    budget: dict[str, Any] | None = None,
    policy: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if has_evolve_state(root):
        raise EvolveError(f"evolve state already exists: {evolve_path(root)}")
    state = {
        "schema_version": EVOLVE_SCHEMA,
        "goal": goal,
        "created_at": now_iso(),
        "budget": {**DEFAULT_BUDGET, **(budget or {})},
        "policy": {**DEFAULT_POLICY, **(policy or {})},
        "run": {"started_at": None},
        "nodes": [],
        "direction_pool": [],
        "boundary": [],
    }
    adopt_family_nodes(root, state)
    # The root's own analysis (if any) is the pool's starting material.
    analysis = read_json(root / "generalize" / "analysis.json", default=None)
    if isinstance(analysis, dict):
        ingest_analysis_directions(
            root,
            state,
            source_node=project_id_from_path(root),
            analysis=analysis,
            analysis_dir="generalize",
        )
    write_state(root, state)
    try:
        from iteris.guide.index import refresh_project_index

        refresh_project_index(root)
    except Exception:
        pass
    return state


# --------------------------------------------------------------------------- #
# Nodes: the lineage tree, reconstructed from sibling lineage snapshots
# --------------------------------------------------------------------------- #


def _lineage(project: Path) -> dict[str, Any]:
    data = read_json(project / ".iteris" / "generalize.json", default={})
    return data if isinstance(data, dict) else {}


def family_member_dirs(root: Path) -> list[Path]:
    """Sibling Iteris projects whose parent chain reaches ``root``.

    Membership is decided by ``evolve_root`` when present, else by walking
    ``parent_project.path`` links (pre-evolve generations lack the pointer).
    """
    root = root.resolve()
    members: list[Path] = []
    for candidate in sorted(root.parent.iterdir()):
        if candidate.resolve() == root or not (candidate / ".iteris").is_dir():
            continue
        seen: set[Path] = set()
        current = candidate.resolve()
        while current not in seen:
            seen.add(current)
            lineage = _lineage(current)
            entry = lineage.get("evolve_root")
            if isinstance(entry, dict) and entry.get("path"):
                if Path(str(entry["path"])).resolve() == root:
                    members.append(candidate)
                break
            parent = lineage.get("parent_project", {}).get("path")
            if not parent:
                break
            current = Path(str(parent)).resolve()
            if current == root:
                members.append(candidate)
                break
    return members


def _direction_kind(project: Path) -> str:
    title = str(_lineage(project).get("direction", {}).get("title", "")).lower()
    return "instantiate" if "instantiate" in title else "abstract"


def adopt_family_nodes(root: Path, state: dict[str, Any]) -> list[dict[str, Any]]:
    """Refresh ``state['nodes']`` from the directory tree, preserving runtime
    fields (started_at, last_progress_at) for already-known nodes."""
    known = {node.get("project"): node for node in state.get("nodes", [])}
    nodes: list[dict[str, Any]] = []
    for member in family_member_dirs(root):
        rel = f"../{member.name}"
        previous = known.get(rel, {})
        nodes.append(
            {
                "project": rel,
                "node_id": project_id_from_path(member),
                "kind": previous.get("kind") or _direction_kind(member),
                "parent": str(_lineage(member).get("parent_project", {}).get("name", "")),
                "seeded_from_direction": previous.get("seeded_from_direction"),
                "started_at": previous.get("started_at"),
                "last_progress_at": previous.get("last_progress_at"),
                "analyzed": bool(previous.get("analyzed")),
            }
        )
    state["nodes"] = nodes
    return nodes


def node_root(root: Path, node: dict[str, Any]) -> Path:
    return (root / str(node["project"])).resolve()


# --------------------------------------------------------------------------- #
# Direction pool
# --------------------------------------------------------------------------- #


def _direction_id(source_node: str, direction: dict[str, Any]) -> str:
    # session_slug, not plain truncation: source nodes sharing a 24-char
    # prefix (probe-drop-*, ...) plus numeric direction ids ("01") collided,
    # so a node's fresh analysis silently deduped against a sibling's old
    # (even vetoed) directions and ingested nothing.
    raw = str(direction.get("id") or direction.get("title") or "direction")
    return f"dir-{session_slug(source_node, 24)}-{slugify(raw, 40)}"


def ingest_analysis_directions(
    root: Path,
    state: dict[str, Any],
    *,
    source_node: str,
    analysis: dict[str, Any],
    analysis_dir: str,
) -> list[dict[str, Any]]:
    """Add a node's ``generalize/analysis.json`` directions to the pool as
    ``proposed`` (veto window opens now). Already-known direction ids and
    directions already attempted by an existing node are skipped."""
    pool = state.setdefault("direction_pool", [])
    known = {entry.get("direction_id") for entry in pool}
    window = int(state.get("policy", {}).get("seed_veto_window_minutes", 60))
    opened = datetime.now(timezone.utc)
    vetoable_until = (opened + timedelta(minutes=window)).isoformat().replace("+00:00", "Z")
    added: list[dict[str, Any]] = []
    for direction in analysis.get("directions", []) or []:
        direction_id = _direction_id(source_node, direction)
        if direction_id in known:
            continue
        markdown = str(direction.get("markdown_file") or "")
        entry = {
            "direction_id": direction_id,
            "source_node": source_node,
            "title": direction.get("title"),
            "markdown_file": f"{analysis_dir}/{Path(markdown).name}" if markdown else None,
            "kind": direction.get("kind"),
            "uses_inputs": direction.get("uses_inputs") or [],
            "scores": direction.get("scores") or {},
            "tier": direction.get("tier"),
            "status": "proposed",
            "rank": None,
            "rank_decision": None,
            "proposed_at": now_iso(),
            "vetoable_until": vetoable_until if window > 0 else None,
        }
        for key in (*CONTRACT_FIELDS, "target_statement", "regularization_target", "first_steps"):
            if direction.get(key) is not None:
                entry[key] = direction[key]
        pool.append(entry)
        known.add(direction_id)
        added.append(entry)
    return added


def find_direction(state: dict[str, Any], direction_id: str) -> dict[str, Any]:
    for entry in state.get("direction_pool", []):
        if entry.get("direction_id") == direction_id:
            return entry
    raise EvolveError(f"unknown direction: {direction_id}")


def set_direction_status(
    state: dict[str, Any], direction_id: str, status: str, **fields: Any
) -> dict[str, Any]:
    if status not in DIRECTION_STATUSES:
        raise EvolveError(f"invalid direction status: {status}")
    entry = find_direction(state, direction_id)
    if entry.get("status") == "vetoed" and status != "vetoed":
        raise EvolveError(f"direction is vetoed by the human: {direction_id}")
    entry["status"] = status
    entry.update(fields)
    return entry


def veto_direction(root: Path, direction_id: str, *, why: str | None = None) -> dict[str, Any]:
    state = read_state(root)
    entry = find_direction(state, direction_id)
    if entry.get("status") not in {"proposed", "approved"}:
        raise EvolveError(
            f"only proposed/approved directions can be vetoed (status: {entry.get('status')})"
        )
    entry["status"] = "vetoed"
    entry["vetoed_at"] = now_iso()
    if why and why.strip():
        # Fed back into analyze/revise prompts as the anti-repetition signal:
        # without the why, the same genre reappears in every analysis batch.
        entry["vetoed_why"] = why.strip()
    write_state(root, state)
    return entry


_CONTRACT_HEADINGS = {
    "## success criteria": "success_criteria",
    "## what does not count": "does_not_count",
    "## audit routes": "audit_routes",
    "## experiment gate": "experiment_gate",
}


def contract_from_markdown(text: str) -> dict[str, Any]:
    """Extract structured contract fields from a direction document's sections.

    Used by ``propose_direction`` so a human-written file with authored
    contract sections also gets the goal-level contract clauses at seed time
    (the goal text is what shapes the worker before verification ever runs).
    Bullets become list items; a bulletless section contributes its prose.
    """
    contract: dict[str, Any] = {}
    field: str | None = None
    prose: list[str] = []

    def _flush() -> None:
        nonlocal field, prose
        if field:
            text_block = "\n".join(prose).strip()
            if field == "experiment_gate":
                if text_block:
                    contract[field] = text_block
            elif not contract.get(field) and text_block:
                contract[field] = [text_block]
        field, prose = None, []

    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            _flush()
            field = _CONTRACT_HEADINGS.get(stripped.lower())
            continue
        if field is None:
            continue
        if stripped.startswith("- ") and field != "experiment_gate":
            contract.setdefault(field, []).append(stripped[2:].strip())
        elif stripped:
            prose.append(stripped)
    _flush()
    return contract


def propose_direction(
    root: Path,
    *,
    markdown: Path,
    rank: int | None = None,
    kind: str = "abstract",
    approve: bool = False,
) -> dict[str, Any]:
    """First-class pool entry for a human-written direction file.

    Replaces the stop -> hand-edit EVOLVE.json -> validate -> run SOP. The
    markdown file is copied under the root's ``generalize/`` when it lives
    outside the root, so seeding always resolves it root-relatively. The entry
    is marked ``human_injected`` and skips the veto window (the human IS the
    veto authority) — it enters as ``proposed`` unless ``approve`` is set.
    """
    if kind not in {"abstract", "instantiate"}:
        raise EvolveError(f"kind must be abstract|instantiate, got: {kind}")
    markdown = markdown.expanduser().resolve()
    if not markdown.is_file():
        raise EvolveError(f"direction file not found: {markdown}")
    body = markdown.read_text(encoding="utf-8", errors="replace")
    state = read_state(root)
    root = root.resolve()
    try:
        rel = str(markdown.relative_to(root))
        copy_to: Path | None = None
    except ValueError:
        copy_to = root / "generalize" / markdown.name
        if copy_to.is_dir():
            raise EvolveError(f"generalize/{markdown.name} exists and is a directory")
        if copy_to.is_file() and copy_to.read_text(encoding="utf-8", errors="replace") != body:
            raise EvolveError(
                f"a different generalize/{markdown.name} already exists; rename the file"
            )
        rel = str(copy_to.relative_to(root))
    title = markdown.stem
    for line in body.splitlines():
        if line.strip().startswith("# "):
            title = line.strip()[2:].strip()
            break
    # Id from the root-relative path: distinct files with the same stem stay
    # distinct, and re-proposing the same file stays idempotent.
    direction_id = _direction_id("human", {"id": rel})
    pool = state.setdefault("direction_pool", [])
    if any(e.get("direction_id") == direction_id for e in pool):
        raise EvolveError(f"direction already in pool: {direction_id}")
    if copy_to is not None:
        copy_to.parent.mkdir(parents=True, exist_ok=True)
        copy_to.write_text(body, encoding="utf-8")
    entry = {
        "direction_id": direction_id,
        "source_node": "human",
        "title": title,
        "markdown_file": rel,
        "kind": kind,
        "uses_inputs": [],
        "scores": {},
        "tier": None,
        "status": "approved" if approve else "proposed",
        "rank": rank,
        "rank_decision": None,
        "proposed_at": now_iso(),
        "vetoable_until": None,
        "human_injected": True,
    }
    entry.update(contract_from_markdown(body))
    pool.append(entry)
    write_state(root, state)
    return entry


def unseeded_open(state: dict[str, Any]) -> list[str]:
    """Approved/proposed directions that no node has been seeded from yet.

    Surfaced by ``evolve stop``/``status`` so a stopped supervisor does not
    silently strand an approved pool (observed in real runs: 3 approved
    directions lapsed unseeded with no warning)."""
    return [
        str(e.get("direction_id"))
        for e in state.get("direction_pool", [])
        if e.get("status") in {"proposed", "approved"}
    ]


def approve_lapsed(state: dict[str, Any], *, now: datetime | None = None) -> list[str]:
    """Mechanically promote ``proposed`` directions whose veto window lapsed."""
    reference = now or datetime.now(timezone.utc)
    approved: list[str] = []
    for entry in state.get("direction_pool", []):
        if entry.get("status") != "proposed":
            continue
        until = entry.get("vetoable_until")
        if until is None:
            entry["status"] = "approved"
            approved.append(entry["direction_id"])
            continue
        try:
            deadline = datetime.fromisoformat(str(until).replace("Z", "+00:00"))
        except ValueError:
            continue
        if reference >= deadline:
            entry["status"] = "approved"
            approved.append(entry["direction_id"])
    return approved


def schedulable_directions(state: dict[str, Any]) -> list[dict[str, Any]]:
    """Approved directions, best first: explicit rank, then abstract bias,
    then tier. The ordering is judgment (rerank_directions); this fallback
    keeps the pool deterministic before any judgment has run."""
    abstract_bias = bool(state.get("policy", {}).get("abstract_bias", True))

    def sort_key(entry: dict[str, Any]) -> tuple:
        rank = entry.get("rank")
        kind_bias = 0 if (abstract_bias and entry.get("kind") == "abstract") else 1
        return (
            0 if rank is not None else 1,
            rank if rank is not None else 0,
            kind_bias,
            entry.get("tier") or 9,
            str(entry.get("direction_id")),
        )

    return sorted(
        (e for e in state.get("direction_pool", []) if e.get("status") == "approved"),
        key=sort_key,
    )


# --------------------------------------------------------------------------- #
# Budget
# --------------------------------------------------------------------------- #


def budget_status(state: dict[str, Any], *, now: datetime | None = None) -> dict[str, Any]:
    budget = state.get("budget", {})
    started = state.get("run", {}).get("started_at")
    spent = float(budget.get("spent_hours", 0.0))
    if started:
        try:
            begun = datetime.fromisoformat(str(started).replace("Z", "+00:00"))
            spent = max(spent, ((now or datetime.now(timezone.utc)) - begun).total_seconds() / 3600.0)
        except ValueError:
            pass
    wall = float(budget.get("wall_hours", 0.0))
    running = [e for e in state.get("direction_pool", []) if e.get("status") == "running"]
    return {
        "wall_hours": wall,
        "spent_hours": round(spent, 3),
        "remaining_hours": round(max(0.0, wall - spent), 3),
        "exhausted": spent >= wall,
        "running": len(running),
        "max_concurrent": int(budget.get("max_concurrent", 1)),
        "slots_free": max(0, int(budget.get("max_concurrent", 1)) - len(running)),
        "node_stall_hours": float(budget.get("node_stall_hours", 18.0)),
        "max_nodes": int(budget.get("max_nodes", 12)),
        "nodes": len(state.get("nodes", [])),
    }


def record_boundary(
    state: dict[str, Any],
    *,
    direction_id: str,
    verdict: str,
    reason_summary: str,
    evidence: list[str] | None = None,
) -> dict[str, Any]:
    entry = {
        "direction_id": direction_id,
        "verdict": verdict,
        "reason_summary": reason_summary,
        "evidence": evidence or [],
        "recorded_at": now_iso(),
    }
    state.setdefault("boundary", []).append(entry)
    return entry

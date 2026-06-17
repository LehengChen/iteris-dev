"""Generalization analysis: have a Codex agent read a verified result and emit a
structured set of generalization directions.

This closes the loop with `iteris generalize`: `analyze` (producer) writes a
schema-conforming `generalize/analysis.json` plus one markdown file per direction,
and `iteris generalize --direction generalize/auto-NN-<slug>.md` (consumer) seeds a
project from any of them.

The schema bakes in lessons from real generalization runs:
- It forces the agent to factor the proof into *load-bearing* abstract inputs vs
  *incidental* instance-specific machinery (the core skill behind a good
  generalization).
- Every direction carries ``kind`` (abstract | instantiate), which downstream maps
  to the generalization goal mode.
- Every ``instantiate`` direction must name a precise ``regularization_target`` so
  the consumer does not stall on a literally-singular object and exit with a
  premature "not applicable" (the failure mode observed on the bosonic kernel).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

ANALYZE_SCHEMA = "iteris.generalize_analysis.v0"
ANALYZE_AXES = {"A_theory", "B_algorithm", "C_transfer", "application", "hardening"}
DIRECTION_KINDS = {"abstract", "instantiate"}
SCORE_KEYS = {"impact", "tractability", "reuse", "risk"}
SCORE_VALUES = {"H", "M", "L", "H-M", "M-H", "M-L", "L-M"}

# Structured success-contract fields a direction may carry. They flow from the
# analysis/synthesis schema into the evolve pool, into the seeded child's
# sources/<direction>.md, and into the goal_success verification contract.
# Lessons from supervised runs: weak "generalize along X" goals get satisfied
# by the cheapest warm-up sub-problem, and disjunctive criteria get satisfied
# by the cheapest disjunct — audit-style (conjunctive) contracts are the
# effective fix.
CONTRACT_FIELDS = (
    "success_criteria",
    "does_not_count",
    "audit_routes",
    "experiment_gate",
    "novelty_claim",
)


def validate_contract_fields(direction: dict[str, Any], where: str) -> list[str]:
    """Validate the optional structured success-contract fields on a direction.

    Shared by the analysis schema and the evolve supervisor's synthesis-
    direction validator so both entry paths enforce the same shape.
    """
    errors: list[str] = []
    for key in ("success_criteria", "does_not_count"):
        value = direction.get(key)
        if value is None:
            continue
        if not isinstance(value, list) or not value:
            errors.append(f"{where}.{key} must be a non-empty list when present")
        elif not all(isinstance(item, str) and item.strip() for item in value):
            errors.append(f"{where}.{key} items must be non-empty strings")
    routes = direction.get("audit_routes")
    if routes is not None:
        if not isinstance(routes, list) or not routes:
            errors.append(f"{where}.audit_routes must be a non-empty list when present")
        else:
            for i, route in enumerate(routes):
                if isinstance(route, str) and route.strip():
                    continue
                if isinstance(route, dict) and str(route.get("route", "")).strip():
                    continue
                errors.append(
                    f"{where}.audit_routes[{i}] must be a non-empty string or an object with a route"
                )
    for key in ("experiment_gate", "novelty_claim"):
        value = direction.get(key)
        if value is not None and (not isinstance(value, str) or not value.strip()):
            errors.append(f"{where}.{key} must be a non-empty string when present")
    return errors


def _err(errors: list[str], where: str, msg: str) -> None:
    errors.append(f"{where}: {msg}")


def validate_analysis(payload: Any) -> dict[str, Any]:
    """Validate an analysis payload against ``iteris.generalize_analysis.v0``.

    Returns ``{"ok": bool, "errors": [str], "direction_count": int}``. Hand-rolled
    (no jsonschema dependency), matching the style of ``memory/facts.py``.
    """
    errors: list[str] = []
    if not isinstance(payload, dict):
        return {"ok": False, "errors": ["payload is not an object"], "warnings": [], "direction_count": 0}

    if payload.get("schema_version") != ANALYZE_SCHEMA:
        _err(errors, "schema_version", f"must equal {ANALYZE_SCHEMA!r}")

    parent = payload.get("parent_project")
    if not isinstance(parent, dict) or not parent.get("name"):
        _err(errors, "parent_project", "must be an object with a name")

    for key in ("source_result", "result_summary"):
        if not isinstance(payload.get(key), str) or not payload.get(key, "").strip():
            _err(errors, key, "must be a non-empty string")

    lbi = payload.get("load_bearing_inputs")
    if not isinstance(lbi, list) or not lbi:
        _err(errors, "load_bearing_inputs", "must be a non-empty list")
    else:
        for i, item in enumerate(lbi):
            if not isinstance(item, dict) or not item.get("key") or not item.get("statement"):
                _err(errors, f"load_bearing_inputs[{i}]", "needs key and statement")

    if not isinstance(payload.get("incidental_machinery"), list):
        _err(errors, "incidental_machinery", "must be a list")

    directions = payload.get("directions")
    if not isinstance(directions, list) or not directions:
        _err(errors, "directions", "must be a non-empty list")
        directions = []

    declared_keys = {
        item.get("key")
        for item in (lbi if isinstance(lbi, list) else [])
        if isinstance(item, dict) and item.get("key")
    }
    seen_ids: set[str] = set()
    for i, d in enumerate(directions):
        where = f"directions[{i}]"
        if not isinstance(d, dict):
            _err(errors, where, "must be an object")
            continue
        did = d.get("id")
        if not did or not isinstance(did, str):
            _err(errors, where, "needs a string id")
        elif did in seen_ids:
            _err(errors, where, f"duplicate id {did!r}")
        else:
            seen_ids.add(did)
        if not d.get("title"):
            _err(errors, where, "needs a title")
        if d.get("kind") not in DIRECTION_KINDS:
            _err(errors, where, f"kind must be one of {sorted(DIRECTION_KINDS)}")
        if d.get("axis") is not None and d.get("axis") not in ANALYZE_AXES:
            _err(errors, where, f"axis must be one of {sorted(ANALYZE_AXES)}")
        if not isinstance(d.get("target_statement"), str) or not d.get("target_statement", "").strip():
            _err(errors, where, "needs a non-empty target_statement")
        if not isinstance(d.get("first_steps"), list) or not d.get("first_steps"):
            _err(errors, where, "needs a non-empty first_steps list")
        # uses_inputs must reference declared load-bearing keys (no dangling pillars).
        uses = d.get("uses_inputs")
        if uses is not None:
            if not isinstance(uses, list):
                _err(errors, where, "uses_inputs must be a list")
            else:
                for key in uses:
                    if key not in declared_keys:
                        _err(errors, where, f"uses_inputs references undeclared load_bearing key {key!r}")
        # The bosonic lesson: instantiation directions must pin a precise object.
        if d.get("kind") == "instantiate":
            rt = d.get("regularization_target")
            if not isinstance(rt, str) or not rt.strip():
                _err(errors, where, "instantiate directions need a non-empty regularization_target")
        scores = d.get("scores")
        if not isinstance(scores, dict):
            _err(errors, where, "needs a scores object")
        else:
            for sk in SCORE_KEYS:
                if sk not in scores:
                    _err(errors, where, f"scores missing {sk}")
        errors.extend(validate_contract_fields(d, where))

    # Cross-direction checks: depends_on and recommended_order must reference real ids.
    for i, d in enumerate(directions):
        if not isinstance(d, dict):
            continue
        deps = d.get("depends_on")
        if isinstance(deps, list):
            for dep in deps:
                if dep not in seen_ids:
                    _err(errors, f"directions[{i}]", f"depends_on references unknown direction id {dep!r}")
    order = payload.get("recommended_order")
    if order is not None:
        if not isinstance(order, list):
            _err(errors, "recommended_order", "must be a list")
        elif set(order) != seen_ids:
            _err(errors, "recommended_order", "must be a permutation of the direction ids")

    # Non-fatal: a direction without a success contract is schedulable but gets
    # satisfied by its cheapest warm-up sub-problem (the observed failure mode).
    warnings: list[str] = []
    for i, d in enumerate(directions):
        if not isinstance(d, dict):
            continue
        for key in ("success_criteria", "does_not_count"):
            if d.get(key) is None:
                warnings.append(f"directions[{i}]: no {key} — weak goal contract")

    return {
        "ok": not errors,
        "errors": errors,
        "warnings": warnings,
        "direction_count": len(directions),
    }


def validate_analysis_file(path: Path) -> dict[str, Any]:
    """Validate an analysis JSON file and that each direction's md file exists."""
    if not path.is_file():
        return {"ok": False, "errors": [f"file not found: {path}"], "warnings": [], "direction_count": 0}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        return {"ok": False, "errors": [f"invalid JSON: {exc}"], "warnings": [], "direction_count": 0}
    result = validate_analysis(payload)
    # Cross-check that referenced direction markdown files exist alongside.
    base = path.parent
    missing: list[str] = []
    for d in payload.get("directions", []) if isinstance(payload, dict) else []:
        md = d.get("markdown_file") if isinstance(d, dict) else None
        if md and not (base / md if not Path(md).is_absolute() else Path(md)).exists():
            # Also accept paths relative to the project root (base is generalize/).
            if not (base.parent / md).exists():
                missing.append(md)
    if missing:
        result = {**result, "ok": False, "errors": [*result["errors"], *(f"missing markdown_file: {m}" for m in missing)]}
    return result


SCHEMA_TEMPLATE = """{
  "schema_version": "iteris.generalize_analysis.v0",
  "parent_project": {"path": "<abs path>", "id": "<project id>", "name": "<dir name>"},
  "source_result": "<parent-relative path to the verified result>",
  "result_summary": "<1-2 sentence plain-language statement of the verified result>",
  "load_bearing_inputs": [
    {"key": "STP", "statement": "<the abstract property actually used>", "used_for": "<where the proof uses it>", "kernel_specific": false}
  ],
  "incidental_machinery": ["<instance-specific tools that are NOT load-bearing, e.g. the specific Bessel/dyadic estimates for one kernel>"],
  "directions": [
    {
      "id": "01",
      "title": "<short title>",
      "axis": "A_theory | B_algorithm | C_transfer | application | hardening",
      "kind": "abstract | instantiate",
      "one_line": "<one-line summary>",
      "target_statement": "<the precise generalized theorem or target to prove>",
      "uses_inputs": ["STP", "ENV"],
      "regularization_target": "<REQUIRED for kind=instantiate: the precise corrected/regularized object to use, so a literally-singular object does not cause a premature 'not applicable'>",
      "first_steps": ["<concrete first step>", "..."],
      "risks": ["<honest caveat>"],
      "depends_on": ["<other direction id>"],
      "success_criteria": ["<checkable condition the result must satisfy>", "..."],
      "does_not_count": ["<already-verified warm-up result that does NOT satisfy this direction>"],
      "audit_routes": ["<OPTIONAL, for deep-push directions: route that must receive an explicit verdict>"],
      "experiment_gate": "<OPTIONAL: numerical experiment that must run and be archived before proof attempts>",
      "novelty_claim": "<which hypothesis/object/result OUTSIDE the existing pool this direction uses>",
      "scores": {"impact": "H|M|L", "tractability": "H|M|L", "reuse": "H|M|L", "risk": "H|M|L"},
      "tier": 1,
      "markdown_file": "generalize/auto-01-<slug>.md"
    }
  ],
  "recommended_order": ["07", "01", "02"]
}"""


def build_analyze_prompt(
    *,
    parent_name: str,
    source_result_rel: str,
    n_directions: int,
    analysis_json_path: str,
    directions_dir: str,
    validate_command: str,
    family_digest: str | None = None,
) -> str:
    """Build the analysis prompt for the Codex agent.

    The prompt enforces the three things a good generalization analysis needs:
    (1) factor load-bearing abstract inputs vs incidental machinery; (2) tag each
    direction with ``kind`` and force ``instantiate`` directions to name a precise
    regularization target; (3) emit strictly schema-conforming output plus one
    feed-ready markdown file per direction.
    """
    return f"""You are the Iteris Generalization-Analysis agent.

Your job: read this project's verified result and produce a structured set of
high-quality GENERALIZATION DIRECTIONS that a separate command
(`iteris generalize`) can later seed new projects from. You are NOT proving any
generalization here; you are mapping where the result can go and how.

Authoritative inputs to read first:
- The verified result: `{source_result_rel}` (read it in full).
- The durable facts: `memory/facts/` (especially `status: verified` facts) and
  `memory/facts/FACT_INDEX.jsonl`.
- `STATUS.md`, `ROADMAP.md`, and the source problem under `sources/`.
- Run `iteris tool context . --json` for an overview.

Method (do these in order):

1. STATE THE RESULT. Write a 1-2 sentence plain statement of what is proved.

2. FACTOR THE PROOF. This is the most important step. Separate:
   - `load_bearing_inputs`: the abstract properties the proof actually relies on
     (the load-bearing pillars). For each, give a `key`, a precise `statement`,
     what it is `used_for`, and whether it is `kernel_specific`. For `used_for`,
     point to the concrete place in the proof where the pillar is invoked, so the
     classification is evidence-based, not guessed. Most strong generalizations
     come from realizing these pillars are not tied to the specific object.
   - `incidental_machinery`: tools used only to establish those pillars for THIS
     instance (e.g. one kernel's specific analytic estimates). These are NOT
     load-bearing; a generalization replaces them, not the pillars. Sanity-check
     each: confirm it only serves to establish a pillar for this instance and is
     not silently used elsewhere as a load-bearing step. If unsure whether
     something is load-bearing, treat it as load-bearing.

3. PROPOSE {n_directions} DIRECTIONS spanning these axes where sensible:
   A_theory (more general theorem), B_algorithm (methods from the proof),
   C_transfer (export techniques to adjacent fields), application, hardening.
   For each direction set `kind`:
   - `abstract`: weaken/abstract the hypotheses upward (e.g. to a class of
     objects). These tend to yield positive results.
   - `instantiate`: apply the result/meta-theorem to a concrete new object.
     For EVERY `instantiate` direction you MUST fill `regularization_target`
     with the precise corrected object to work with, AND state why the literal
     object fails (e.g. unbounded, singular, out of scope) and how the fix
     restores each hypothesis the meta-theorem needs. If the literal object is
     singular or out of scope, name the standard fix (subtract a pole, reweight,
     change measure, restrict domain) and target that — do NOT propose a
     direction whose intended outcome is merely "not applicable".
   For each direction also give: `target_statement` (the precise theorem/target),
   `uses_inputs` (a subset of the `key`s you declared in load_bearing_inputs —
   do not invent keys), `first_steps`, honest `risks`, `depends_on` (existing
   direction ids only), `scores` (impact/tractability/reuse/risk in H|M|L), and a
   `tier`.

4. WRITE THE SUCCESS CONTRACT for every direction. A goal like "generalize
   along X" gets satisfied by the cheapest warm-up sub-problem; the contract is
   what prevents that:
   - `success_criteria` (REQUIRED): checkable conditions the result must
     satisfy. Prefer CONJUNCTIVE criteria — a disjunctive list ("prove A or B
     or C") gets satisfied by the cheapest disjunct.
   - `does_not_count` (REQUIRED): name the already-verified warm-up results
     and trivial special cases that do NOT satisfy this direction (e.g. "the
     product-kernel tensorization does not count for the d>=2 direction").
   - `audit_routes` (for deep-push directions): enumerate the known routes;
     the worker must give EVERY route an explicit verdict — a proof or a
     precisely-witnessed obstruction — plus a final synthesis verdict, and may
     not close on the first positive result. Re-proving known results does not
     count.
   - `experiment_gate` (when a cheap numerical experiment can discriminate
     between outcomes): the experiment that must run FIRST, with script, data,
     and a scope statement archived; its verdict allocates proof/refutation
     effort.
   OMIT optional fields you do not use — never emit empty strings or empty
   lists for them.

5. RANK. Provide `recommended_order`: a permutation of ALL direction ids, best
   first.

Outputs (write both):

A. `{analysis_json_path}` — a single JSON object conforming EXACTLY to this schema:

```json
{SCHEMA_TEMPLATE}
```

B. For each direction, a standalone markdown file at the `markdown_file` path
   under `{directions_dir}` (named `auto-<id>-<slug>.md`). Each file must be
   directly usable as `iteris generalize --direction <file>`, so it must contain:
   an H1 title; a one-line summary; a `## Target statement` section with the
   precise generalized theorem; for instantiate directions a `## Regularized
   object` section restating `regularization_target`; a `## Success criteria`
   section and a `## What does NOT count` section restating the contract fields
   (the goal-success verifier reads THIS file — criteria stated only in the JSON
   are invisible to it); when `audit_routes` is set, a `## Audit routes` section
   listing every route; a `## First steps` section; and a `## Risks` section.
   Do not put stale cross-project relative links in these files.

Self-check before finishing:
- Run `{validate_command}` and fix any reported errors. Repeat until it reports
  `ok: true`.
- Confirm every `markdown_file` referenced in the JSON actually exists.

Keep the analysis grounded in what the proof actually uses; avoid generic
literature lists. When done, print a short summary of the directions and their
recommended order. This is an analysis deliverable, not a proof run — stop once
the JSON validates and all direction files exist.
{_family_digest_section(family_digest)}"""


def _family_digest_section(family_digest: str | None) -> str:
    if not family_digest or not family_digest.strip():
        return ""
    indented = "\n".join(f"  {line}" for line in family_digest.strip().splitlines())
    return f"""
Family context (this project belongs to an evolve family):
{indented}

Do NOT propose directions duplicating ones already in the family pool above —
two directions overlap when they weaken the same load-bearing input or target
the same object class. Directions whose genre was vetoed or superseded above
(see the reasons) are OFF-LIMITS: do not re-propose them under a new name.
Because this analysis sees only one node while the pool spans the family,
every direction you propose MUST fill `novelty_claim` naming the hypothesis,
object, or verified result OUTSIDE the pool above that it uses — a direction
that cannot name one is framework-internal reshuffling and must be dropped.
DO consider cross-branch synthesis directions that combine verified results
from different family branches; they are often the highest-value proposals.
"""

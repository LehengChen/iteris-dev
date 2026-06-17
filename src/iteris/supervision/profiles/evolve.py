"""The evolve profile: one master agent driving a family of generalizations.

The master reads each child's PUBLISHED state only (STATUS.md, fact index,
scratch deltas, message acks), judges through five bounded contracts, and acts
through allowlisted actuators. Budget caps are enforced mechanically in the
scheduler actuator — no judgment output can override them.

External processes (seeding, runs, analyze) are invoked through ``run_cli``,
a module-level hook tests monkeypatch; the engine's ``dry_run`` mode journals
intents without executing anything.
"""

from __future__ import annotations

import json
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from iteris.evolve import (
    DEFAULT_POLICY,
    approve_lapsed,
    budget_status,
    evolve_root_entry,
    find_direction,
    ingest_analysis_directions,
    node_root,
    read_state,
    record_boundary,
    set_direction_status,
    write_state,
)
from iteris.executors import resolve_executor
from iteris.generalize_analyze import CONTRACT_FIELDS, validate_contract_fields
from iteris.memory.family import (
    load_family_index,
    record_failed_path,
    update_inputs,
    upsert_family_entries,
)
from iteris.project import now_iso, now_stamp, read_json, session_slug, slugify
from iteris.supervision.actions import CallableActuator, send_message_actuator
from iteris.supervision.contracts import Action, JudgmentContract
from iteris.supervision.engine import Profile
from iteris.supervision.events import Observation, SupervisionContext, TriggerRule
from iteris.supervision.sensors import _status_fields, tmux_session_alive

VERIFIED_PHASES = {"goal_success_verified"}
REDUCED_PHASES = {"principled_stop_certified"}
BLOCKED_PHASES = {"blocked"}


def _analysis_direction_count(state: dict[str, Any]) -> int:
    raw = state.get("policy", {}).get(
        "analysis_directions_per_node",
        DEFAULT_POLICY["analysis_directions_per_node"],
    )
    try:
        count = int(raw)
    except (TypeError, ValueError):
        count = DEFAULT_POLICY["analysis_directions_per_node"]
    return max(1, count)


def goal_success_verified(project: Path) -> bool:
    """Mechanical verified check against the verification ledger.

    Worker STATUS.md phase strings are free-form prose, not a contract —
    real runs write "verified", "complete", "verified-complete", ... .
    The contract is a passing goal_success verification whose target
    artifact exists, so detect that directly."""
    results_dir = project / "verification" / "results"
    if not results_dir.exists():
        return False
    for path in results_dir.glob("*.json"):
        payload = read_json(path, default=None)
        if not isinstance(payload, dict):
            continue
        if payload.get("mode") != "goal_success" or payload.get("passed") is not True:
            continue
        target = payload.get("target_artifact")
        if not target or (project / str(target)).exists():
            return True
    return False


def principled_stop_certified(project: Path) -> bool:
    """Mechanical certified-principled-stop check against the verification ledger.

    A child that honestly terminates (full goal unreachable as stated / reduced
    to a named obstruction) carries a passing ``principled_stop`` verification —
    a first-class terminal, NOT a goal_success. The evolve master must treat it
    as terminal so its concurrency slot is released; without this a family of
    principled_stop children deadlocks. Mirrors ``goal_success_verified``."""
    results_dir = project / "verification" / "results"
    if not results_dir.exists():
        return False
    for path in results_dir.glob("*.json"):
        payload = read_json(path, default=None)
        if not isinstance(payload, dict):
            continue
        if payload.get("mode") != "principled_stop" or payload.get("passed") is not True:
            continue
        target = payload.get("target_artifact")
        if not target or (project / str(target)).exists():
            return True
    return False


def run_cli(args: list[str], *, cwd: Path) -> dict[str, Any]:
    """Invoke the iteris CLI as a subprocess and parse JSON output when present.

    Module-level so tests monkeypatch it; every actuator that touches the
    outside world goes through here or through the messages module.
    """
    proc = subprocess.run(
        [sys.executable, "-m", "iteris.cli", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        timeout=600,
    )
    payload: dict[str, Any] = {"returncode": proc.returncode}
    text = proc.stdout.strip()
    if text.startswith("{"):
        try:
            payload["json"] = json.loads(text)
        except json.JSONDecodeError:
            payload["stdout"] = text[-2000:]
    else:
        payload["stdout"] = text[-2000:]
    if proc.returncode != 0:
        payload["stderr"] = proc.stderr[-2000:]
    return payload


def _executor_args() -> list[str]:
    """``--executor <x>`` for child launches, resolved from the master's env.

    The evolve launcher pins $ITERIS_EXECUTOR into os.environ, so children
    already inherit it via run_cli; passing it explicitly is belt-and-suspenders
    so a child still runs on the right backend even if the env is somehow lost.
    """
    return ["--executor", resolve_executor(None)]


# --------------------------------------------------------------------------- #
# Sensors
# --------------------------------------------------------------------------- #


@dataclass
class NodesSensor:
    """Published state of every family node: phase, fact deltas, failed-path
    deltas, worker-session liveness, and progress staleness."""

    root: Path
    name: str = "nodes"

    def observe(self, ctx: SupervisionContext) -> Observation:
        state = read_state(self.root)
        budget = budget_status(state)
        cursor_update: dict[str, Any] = {}
        nodes: list[dict[str, Any]] = []
        now = datetime.now(timezone.utc)
        for node in state.get("nodes", []):
            project = node_root(self.root, node)
            fields = _status_fields(project)
            phase = fields.get("phase")
            index = project / "memory" / "facts" / "FACT_INDEX.jsonl"
            lines = (
                [l for l in index.read_text(encoding="utf-8", errors="replace").splitlines() if l.strip()]
                if index.exists()
                else []
            )
            fact_key = f"nodes:{node['node_id']}:fact_lines"
            seen = int(ctx.cursors.get(fact_key, 0))
            new_facts = []
            for line in lines[seen:]:
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(row, dict):
                    row["_node"] = node["node_id"]
                    new_facts.append(row)
            cursor_update[fact_key] = len(lines)

            failed = project / "memory" / "scratch" / "failed_paths.jsonl"
            flines = (
                [l for l in failed.read_text(encoding="utf-8", errors="replace").splitlines() if l.strip()]
                if failed.exists()
                else []
            )
            failed_key = f"nodes:{node['node_id']}:failed_lines"
            fseen = int(ctx.cursors.get(failed_key, 0))
            new_failed = []
            for line in flines[fseen:]:
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(row, dict):
                    row["_node"] = node["node_id"]
                    new_failed.append(row)
            cursor_update[failed_key] = len(flines)

            session = f"iteris-{session_slug(project.name)}"
            alive = tmux_session_alive(session)
            progress_at = node.get("last_progress_at") or node.get("started_at")
            stalled_hours = None
            if progress_at and not new_facts:
                try:
                    begun = datetime.fromisoformat(str(progress_at).replace("Z", "+00:00"))
                    stalled_hours = (now - begun).total_seconds() / 3600.0
                except ValueError:
                    stalled_hours = None
            analysis_path = project / "generalize" / "analysis.json"
            analyze_session_alive = analysis_path.exists() and tmux_session_alive(
                f"iteris-analyze-{session_slug(project.name)}"
            )
            nodes.append(
                {
                    "node_id": node["node_id"],
                    "project": str(project),
                    "project_rel": node["project"],
                    "kind": node.get("kind"),
                    "phase": phase,
                    "verified": phase in VERIFIED_PHASES or goal_success_verified(project),
                    # A certified principled_stop is terminal too: ``terminal``
                    # is what the slot-release/reap logic keys off so BOTH a
                    # goal_success and a principled_stop free the concurrency
                    # slot. ``verified`` stays goal_success-only (the milestone /
                    # analyze triggers keep their full-success semantics).
                    "reduced": phase in REDUCED_PHASES or principled_stop_certified(project),
                    "terminal": (
                        phase in VERIFIED_PHASES
                        or phase in REDUCED_PHASES
                        or goal_success_verified(project)
                        or principled_stop_certified(project)
                    ),
                    "blocked": phase in BLOCKED_PHASES,
                    "new_facts": new_facts,
                    "new_verified": [r for r in new_facts if r.get("status") == "verified"],
                    "new_failed_paths": new_failed,
                    "session": session,
                    "session_alive": alive,
                    "running_direction": node.get("seeded_from_direction"),
                    "analyzed": bool(node.get("analyzed")),
                    "has_analysis": analysis_path.exists(),
                    "analyze_session_alive": analyze_session_alive,
                    "stalled_hours": stalled_hours,
                    "stall_threshold": budget["node_stall_hours"],
                }
            )
        return Observation(
            sensor=self.name,
            data={"nodes": nodes, "cursor_update": cursor_update},
        )


@dataclass
class PoolSensor:
    root: Path
    name: str = "pool"

    def observe(self, ctx: SupervisionContext) -> Observation:
        state = read_state(self.root)
        pool = state.get("direction_pool", [])
        lapsed = approve_lapsed(json.loads(json.dumps(state)))  # read-only probe on a copy
        unranked = [
            e["direction_id"] for e in pool if e.get("status") == "approved" and e.get("rank") is None
        ]
        open_statuses = {"proposed", "approved", "seeded", "running"}
        return Observation(
            sensor=self.name,
            data={
                "pool": pool,
                "lapsed_proposals": lapsed,
                "unranked_approved": unranked,
                "open_count": sum(1 for e in pool if e.get("status") in open_statuses),
                "goal": state.get("goal"),
                "boundary": state.get("boundary", []),
            },
        )


@dataclass
class BudgetSensor:
    root: Path
    name: str = "budget"

    def observe(self, ctx: SupervisionContext) -> Observation:
        state = read_state(self.root)
        status = budget_status(state)
        previous = float(ctx.cursors.get("budget:spent_seen", 0.0))
        status["crossed_half"] = (
            status["wall_hours"] > 0
            and previous < status["wall_hours"] / 2 <= status["spent_hours"]
        )
        status["cursor_update"] = {"budget:spent_seen": status["spent_hours"]}
        return Observation(sensor=self.name, data=status)


@dataclass
class FamilyLedgerSensor:
    root: Path
    name: str = "family_ledger"

    def observe(self, ctx: SupervisionContext) -> Observation:
        return Observation(
            sensor=self.name,
            data={"entries": load_family_index(self.root)},
        )


# --------------------------------------------------------------------------- #
# Contracts
# --------------------------------------------------------------------------- #


SUBSTANCE_GRADES = {"NEW", "STD", "MAP"}


def _validate_curate(decision: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    entries = decision.get("entries")
    if not isinstance(entries, list):
        return ["entries must be a list (may be empty)"]
    for i, entry in enumerate(entries):
        if not isinstance(entry, dict):
            errors.append(f"entries[{i}] must be an object")
            continue
        if not str(entry.get("origin_fact_id", "")).startswith("fact:"):
            errors.append(f"entries[{i}].origin_fact_id must start with fact:")
        if not str(entry.get("curated_summary", "")).strip():
            errors.append(f"entries[{i}].curated_summary must be non-empty")
        if not isinstance(entry.get("sightings"), list) or not entry["sightings"]:
            errors.append(f"entries[{i}].sightings must be a non-empty list")
        if entry.get("substance") not in SUBSTANCE_GRADES:
            errors.append(f"entries[{i}].substance must be one of NEW|STD|MAP")
    if not isinstance(decision.get("skipped", []), list):
        errors.append("skipped must be a list")
    failed = decision.get("failed_paths", [])
    if not isinstance(failed, list):
        errors.append("failed_paths must be a list")
    boundary = decision.get("boundary_entries", [])
    if not isinstance(boundary, list):
        errors.append("boundary_entries must be a list")
    else:
        for i, item in enumerate(boundary):
            if not isinstance(item, dict):
                errors.append(f"boundary_entries[{i}] must be an object")
                continue
            if not str(item.get("source", "")).strip():
                errors.append(f"boundary_entries[{i}].source must name a direction or node id")
            if item.get("verdict") not in {"impossible", "refuted", "necessary"}:
                errors.append(f"boundary_entries[{i}].verdict must be impossible|refuted|necessary")
            if not str(item.get("reason_summary", "")).strip():
                errors.append(f"boundary_entries[{i}].reason_summary must be non-empty")
            evidence = item.get("evidence")
            if not isinstance(evidence, list) or not evidence:
                errors.append(f"boundary_entries[{i}].evidence must be a non-empty list of fact ids")
    return errors


CURATE_PROMPT = """You are the evolve supervisor's curation judgment for a family of
generalization projects. New facts and failed paths just appeared in child
projects. Decide which belong in the FAMILY ledger: only claims with reuse
value across the family. Be conservative — a missed lead costs little, a
poisoned family prompt costs every future child. NEVER rewrite statements:
summarize and point, preserving explicit assumptions. Include rejected facts
and dead ends — negative intelligence prevents duplicate work.

Grade each curated entry's mathematical SUBSTANCE — a verified fact is not
automatically progress:
- NEW: genuinely new mathematics (a theorem, lower bound, counterexample, or
  witness that did not exist before).
- STD: a standard/textbook technique instantiated in the family's interface
  (organizational value, not new mathematics).
- MAP: problem cartography — a reformulation, an obstruction made explicit, a
  route audit. Restating the original difficulty in new form is MAP, never NEW.
The grade MUST match your substance_why: a why describing cartography, a route
audit, or a restatement grades MAP; a why describing a standard technique
instantiated grades STD. Reserve NEW for entries whose why itself argues a new
theorem, bound, witness, or counterexample.

Also harvest BOUNDARY entries: when a verified fact establishes an
IMPOSSIBILITY, a refutation of a route, or a necessity of a hypothesis, record
it in `boundary_entries` — these are boundary-map deliverables that the
failure-triggered path (stop_harvest) can never see, because the node
SUCCEEDED at proving the impossibility. Do not re-record entries already
present in the boundary input.

Inputs (new fact index rows per node, current family ledger, family goal):
{inputs_json}

Reply with ONLY one JSON object:
{{
  "entries": [
    {{
      "origin_fact_id": "fact:...",
      "claim_summary": "<from the source row>",
      "curated_summary": "<one paragraph WITH explicit assumptions>",
      "family_relevance": "<why other nodes should care>",
      "substance": "NEW|STD|MAP",
      "substance_why": "<one line justifying the grade>",
      "sightings": [
        {{"project": "<node_id>", "fact_id": "fact:...", "status": "verified|reviewed|rejected",
          "assumptions_scope": "<setting>", "verification": "<id or null>"}}
      ]
    }}
  ],
  "failed_paths": [
    {{"source_project": "<node_id>", "record": {{"route": "...", "reason": "..."}}}}
  ],
  "boundary_entries": [
    {{"source": "<direction_id or node_id the evidence came from>",
      "verdict": "impossible|refuted|necessary",
      "reason_summary": "<the impossibility/necessity, with its scope>",
      "evidence": ["fact:..."]}}
  ],
  "skipped": [{{"fact_id": "fact:...", "why": "<instance bookkeeping / no reuse value>"}}]
}}{retry_feedback}"""


def _curate_to_actions(decision: dict[str, Any]) -> list[Action]:
    return [
        Action(
            name="apply_curation",
            params={
                "entries": decision.get("entries", []),
                "failed_paths": decision.get("failed_paths", []),
                "boundary_entries": decision.get("boundary_entries", []),
                "skipped": decision.get("skipped", []),
            },
        )
    ]


def _validate_rerank(decision: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    ranking = decision.get("ranking")
    if not isinstance(ranking, list) or not all(isinstance(x, str) for x in (ranking or [])):
        errors.append("ranking must be a list of direction ids")
    drops = decision.get("drops", [])
    if not isinstance(drops, list):
        errors.append("drops must be a list")
    else:
        for i, drop in enumerate(drops):
            if not isinstance(drop, dict) or not drop.get("id") or not drop.get("why"):
                errors.append(f"drops[{i}] needs id and why")
    return errors


RERANK_PROMPT = """You are the evolve supervisor's direction-ranking judgment. Order the
approved directions in the pool, best first. Policy: abstract directions are
the spine (push generality upward); instantiate directions are quota-bounded
witnesses that keep abstractions non-vacuous. Two directions OVERLAP when
they weaken the same load-bearing input (uses_inputs) or target the same
object class — drop or merge duplicates, citing the survivor. Use the family
ledger and boundary records: do not rank highly a direction whose load-bearing
input already failed in a sibling setting. A direction with no `novelty_claim`
naming an out-of-pool input deserves a low rank — or a drop when the pool
already covers its genre.

Inputs (pool, family ledger, boundary, goal):
{inputs_json}

Reply with ONLY one JSON object:
{{
  "ranking": ["<direction_id best first>", "..."],
  "drops": [{{"id": "<direction_id>", "why": "<overlap with X / input refuted in Y>"}}],
  "overlap_notes": "<short reasoning summary>"
}}{retry_feedback}"""


def _rerank_to_actions(decision: dict[str, Any]) -> list[Action]:
    return [
        Action(
            name="apply_ranking",
            params={
                "ranking": decision.get("ranking", []),
                "drops": decision.get("drops", []),
                "overlap_notes": decision.get("overlap_notes", ""),
            },
        )
    ]


def _validate_diagnose(decision: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    if not isinstance(decision.get("stalled"), bool):
        errors.append("stalled must be a boolean")
    if not str(decision.get("node_id", "")).strip():
        errors.append("node_id must name the diagnosed node")
    if not str(decision.get("diagnosis", "")).strip():
        errors.append("diagnosis must be non-empty")
    if decision.get("recommendation") not in {"report", "message", "stop_harvest"}:
        errors.append("recommendation must be report|message|stop_harvest")
    if decision.get("recommendation") == "message" and not str(decision.get("message_text", "")).strip():
        errors.append("message_text required when recommendation is message")
    return errors


DIAGNOSE_PROMPT = """You are the evolve supervisor's stall-diagnosis judgment for ONE family
node. Decide whether the node is genuinely stuck and what to do. Be
conservative about stop_harvest: slow-burning proofs look quiet; a stop
discards the slot but harvests facts and failed paths first. Prefer
"message" when a concrete steer could unblock (cite family ledger leads or
known dead ends).

Inputs (stalled node snapshots, family ledger):
{inputs_json}

Reply with ONLY one JSON object:
{{
  "stalled": true,
  "node_id": "<node_id of the diagnosed node>",
  "diagnosis": "<what the evidence shows>",
  "recommendation": "report|message|stop_harvest",
  "message_text": "<required when recommendation is message>",
  "boundary_reason": "<required when stop_harvest: why this counts as a boundary>"
}}{retry_feedback}"""


def _diagnose_to_actions(decision: dict[str, Any]) -> list[Action]:
    node_id = str(decision.get("node_id", ""))
    recommendation = decision.get("recommendation")
    if recommendation == "message":
        return [
            Action(
                name="message_node",
                params={
                    "node_id": node_id,
                    "body": str(decision.get("message_text", "")),
                    "priority": "high",
                },
            )
        ]
    if recommendation == "stop_harvest":
        return [
            Action(
                name="stop_harvest",
                params={
                    "node_id": node_id,
                    "reason": decision.get("boundary_reason") or decision.get("diagnosis", ""),
                },
            )
        ]
    return [Action(name="record", params={"node_id": node_id, "diagnosis": decision.get("diagnosis", "")})]


def _validate_stage_report(decision: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    if not str(decision.get("report_markdown", "")).strip():
        errors.append("report_markdown must be non-empty")
    if not str(decision.get("headline", "")).strip():
        errors.append("headline must be non-empty")
    return errors


STAGE_REPORT_PROMPT = """You are the evolve supervisor's stage-report judgment. Write a
SELF-CONTAINED milestone report: a reader with NO access to this workspace
must be able to review it alone. Requirements: restate the family goal; the
tree state (every node, phase, what it proved or where it failed); inline the
key theorem statements VERBATIM from the inputs (never bare fact ids); the
boundary map with reasons; budget spent/remaining; next planned steps. When
ledger entries carry substance grades, summarize family output BY GRADE
([NEW]/[STD]/[MAP]) and keep "theorems" distinct from "maps" — a verified
fact is not automatically mathematical progress.

Inputs (goal, nodes, pool, boundary, family ledger, budget, milestone):
{inputs_json}

Reply with ONLY one JSON object:
{{
  "headline": "<one line>",
  "report_markdown": "<the full self-contained report>"
}}{retry_feedback}"""


def _stage_report_to_actions(decision: dict[str, Any]) -> list[Action]:
    return [
        Action(
            name="write_stage_report",
            params={
                "headline": decision.get("headline", ""),
                "report_markdown": decision.get("report_markdown", ""),
                "milestone": decision.get("_trigger", {}).get("milestone", "milestone"),
            },
        )
    ]


def _validate_revise(decision: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    if not isinstance(decision.get("pool_edits", []), list):
        errors.append("pool_edits must be a list")
    for i, edit in enumerate(decision.get("pool_edits", []) or []):
        if not isinstance(edit, dict) or not edit.get("direction_id"):
            errors.append(f"pool_edits[{i}] needs direction_id")
        elif edit.get("status") not in {None, "superseded", "blocked", "approved"}:
            errors.append(f"pool_edits[{i}].status may only move to superseded|blocked|approved")
    if not isinstance(decision.get("new_synthesis_directions", []), list):
        errors.append("new_synthesis_directions must be a list")
    for i, direction in enumerate(decision.get("new_synthesis_directions", []) or []):
        if not isinstance(direction, dict):
            errors.append(f"new_synthesis_directions[{i}] must be an object")
            continue
        for key in ("title", "kind", "target_statement", "novelty_claim"):
            if not str(direction.get(key, "")).strip():
                errors.append(f"new_synthesis_directions[{i}].{key} must be non-empty")
        if direction.get("kind") == "instantiate" and not str(
            direction.get("regularization_target", "")
        ).strip():
            errors.append(
                f"new_synthesis_directions[{i}]: instantiate requires regularization_target"
            )
        errors.extend(
            validate_contract_fields(direction, f"new_synthesis_directions[{i}]")
        )
    return errors


REVISE_PROMPT = """You are the evolve supervisor's plan-revision judgment, invoked because a
node finished, failed, or stalled out — or because the open direction pool
has grown crowded and needs global consolidation. Revise the direction pool
ACROSS GENERATIONS: supersede directions invalidated by new boundary
evidence, drop near-duplicates and repeatedly-rejected genres (per-node
analyses cannot see pool history, so the same tooling/certificate genre
reappears in every batch — drop repeats), and propose NEW directions the
per-node analyses cannot see — especially CROSS-BRANCH SYNTHESIS
(combining verified results from different branches). Every new direction
needs a precise target_statement; instantiate directions MUST name a
regularization_target. New directions enter as proposed and face the human
veto window.

STRUCTURAL NOVELTY — every new direction MUST fill `novelty_claim` naming
the hypothesis, object, or verified result OUTSIDE the current pool that it
uses. A direction that cannot name one is framework-internal reshuffling:
do not propose it. Genres already vetoed or superseded in the pool (read
their reasons in the inputs) are OFF-LIMITS under any rename.

SUCCESS CONTRACT — give every new direction `success_criteria` (checkable,
prefer conjunctive) and `does_not_count` (name the warm-up results that do
NOT satisfy it); weak contracts get satisfied by the cheapest sub-problem.
For deep pushes against a known obstacle, write an AUDIT contract: set
`audit_routes` to the known routes, each of which must receive a verdict
(proof or witnessed obstruction) — disjunctive criteria get satisfied by
the cheapest disjunct. When a cheap numerical experiment can discriminate
between outcomes, set `experiment_gate`. OMIT optional fields you do not
use — never emit empty strings or empty lists for them.

PORTFOLIO BALANCE — HARD RULE: the family's deliverables are (i) the most
general verified theorem, (ii) a BOUNDARY MAP of where and why
generalization FAILS, and (iii) witnesses. Check trigger.portfolio: when
failed_or_blocked_nodes == 0 AND boundary_entries == 0, your reply is
INVALID unless new_synthesis_directions contains at least one
FALSIFICATION PROBE: a direction whose title starts with "Probe:" and
whose target_statement names the load-bearing hypothesis being removed or
the optimality/lower-bound question being tested (e.g. drop
sign-regularity, drop slab structure, drop compactness, or ask whether the
log-rank law is optimal). A probe that fails with a precise reason is a
boundary-map deliverable, not a wasted slot — the family currently has NO
failure evidence, which means nobody knows where the theorem stops being
true.

Inputs (full evolve state, family ledger, triggering event):
{inputs_json}

Reply with ONLY one JSON object:
{{
  "pool_edits": [{{"direction_id": "dir-...", "status": "superseded", "why": "..."}}],
  "new_synthesis_directions": [
    {{"title": "...", "kind": "abstract|instantiate", "target_statement": "...",
      "uses_inputs": ["KEY"], "regularization_target": "<for instantiate>",
      "novelty_claim": "<the out-of-pool hypothesis/object/result this uses>",
      "success_criteria": ["<checkable condition>"], "does_not_count": ["<warm-up that does not count>"],
      "audit_routes": ["<route requiring a verdict>"], "experiment_gate": "<optional experiment-first gate>",
      "first_steps": ["..."], "scores": {{"impact": "H", "tractability": "M", "reuse": "H", "risk": "M"}}}}
  ],
  "budget_advice": "<optional>",
  "narrative": "<short reasoning>"
}}{retry_feedback}"""


def _revise_to_actions(decision: dict[str, Any]) -> list[Action]:
    return [
        Action(
            name="apply_plan_revision",
            params={
                "pool_edits": decision.get("pool_edits", []),
                "new_synthesis_directions": decision.get("new_synthesis_directions", []),
                "narrative": decision.get("narrative", ""),
            },
        )
    ]


def build_contracts() -> list[JudgmentContract]:
    return [
        JudgmentContract(
            name="curate_facts",
            inputs=["nodes", "family_ledger", "pool"],
            prompt_template=CURATE_PROMPT,
            validator=_validate_curate,
            allowed_actions=["apply_curation"],
            to_actions=_curate_to_actions,
        ),
        JudgmentContract(
            name="rerank_directions",
            inputs=["pool", "family_ledger"],
            prompt_template=RERANK_PROMPT,
            validator=_validate_rerank,
            allowed_actions=["apply_ranking"],
            to_actions=_rerank_to_actions,
        ),
        JudgmentContract(
            name="diagnose_stall",
            inputs=["nodes", "family_ledger"],
            prompt_template=DIAGNOSE_PROMPT,
            validator=_validate_diagnose,
            allowed_actions=["message_node", "stop_harvest", "record"],
            to_actions=_diagnose_to_actions,
        ),
        JudgmentContract(
            name="write_stage_report",
            inputs=["nodes", "pool", "budget", "family_ledger"],
            prompt_template=STAGE_REPORT_PROMPT,
            validator=_validate_stage_report,
            allowed_actions=["write_stage_report"],
            to_actions=_stage_report_to_actions,
        ),
        JudgmentContract(
            name="revise_plan",
            inputs=["nodes", "pool", "budget", "family_ledger"],
            prompt_template=REVISE_PROMPT,
            validator=_validate_revise,
            allowed_actions=["apply_plan_revision"],
            to_actions=_revise_to_actions,
        ),
    ]


# --------------------------------------------------------------------------- #
# Actuators
# --------------------------------------------------------------------------- #


def _approve_directions(action: Action, ctx: SupervisionContext) -> dict[str, Any]:
    state = read_state(ctx.root)
    approved = approve_lapsed(state)
    write_state(ctx.root, state)
    return {"approved": approved}


def _apply_curation(action: Action, ctx: SupervisionContext) -> dict[str, Any]:
    count = upsert_family_entries(ctx.root, action.params.get("entries", []))
    for item in action.params.get("failed_paths", []) or []:
        if isinstance(item, dict) and isinstance(item.get("record"), dict):
            record_failed_path(
                ctx.root,
                source_project=str(item.get("source_project") or "unknown"),
                record=item["record"],
            )
    # Success-path boundary writes: verified impossibility/necessity facts.
    # Without this, EVOLVE.json's boundary only ever fills via stop_harvest
    # (failure/stall) and stays empty in an all-verified family.
    boundary_items = [
        item for item in action.params.get("boundary_entries", []) or [] if isinstance(item, dict)
    ]
    recorded = 0
    if boundary_items:
        state = read_state(ctx.root)
        # record_boundary is append-only and the judgment re-sees the same
        # ledger every tick — dedup here or duplicates accumulate for the
        # rest of the run.
        seen: set[tuple] = set()
        for existing in state.get("boundary", []):
            source = str(existing.get("direction_id"))
            verdict = str(existing.get("verdict"))
            seen.add((source, verdict, str(existing.get("reason_summary"))))
            seen.add((source, verdict, tuple(sorted(existing.get("evidence") or []))))
        for item in boundary_items:
            source = str(item.get("source") or "unknown")
            verdict = str(item.get("verdict") or "impossible")
            reason = str(item.get("reason_summary") or "")
            evidence = sorted(str(x) for x in item.get("evidence") or [])
            if (source, verdict, reason) in seen or (source, verdict, tuple(evidence)) in seen:
                continue
            record_boundary(
                state,
                direction_id=source,
                verdict=verdict,
                reason_summary=reason,
                evidence=evidence,
            )
            seen.add((source, verdict, reason))
            seen.add((source, verdict, tuple(evidence)))
            recorded += 1
        if recorded:
            write_state(ctx.root, state)
    return {
        "curated": count,
        "boundary_recorded": recorded,
        "skipped": len(action.params.get("skipped", [])),
    }


def _apply_ranking(action: Action, ctx: SupervisionContext) -> dict[str, Any]:
    state = read_state(ctx.root)
    ranking = [str(x) for x in action.params.get("ranking", [])]
    decision_ref = action.decision_ref
    applied = 0
    for position, direction_id in enumerate(ranking, start=1):
        try:
            entry = find_direction(state, direction_id)
        except Exception:
            continue
        entry["rank"] = position
        entry["rank_decision"] = decision_ref
        applied += 1
    dropped = []
    for drop in action.params.get("drops", []) or []:
        try:
            entry = find_direction(state, str(drop.get("id")))
        except Exception:
            continue
        if entry.get("status") in {"proposed", "approved"}:
            entry["status"] = "superseded"
            entry["superseded_why"] = drop.get("why")
            entry["rank_decision"] = decision_ref
            dropped.append(entry["direction_id"])
    write_state(ctx.root, state)
    return {"ranked": applied, "dropped": dropped}


def _apply_plan_revision(action: Action, ctx: SupervisionContext) -> dict[str, Any]:
    state = read_state(ctx.root)
    decision_ref = action.decision_ref
    edits = 0
    for edit in action.params.get("pool_edits", []) or []:
        try:
            entry = find_direction(state, str(edit.get("direction_id")))
        except Exception:
            continue
        status = edit.get("status")
        if status in {"superseded", "blocked", "approved"} and entry.get("status") != "vetoed":
            entry["status"] = status
            entry["revision_why"] = edit.get("why")
            entry["rank_decision"] = decision_ref
            edits += 1
    synthesized = ingest_analysis_directions(
        ctx.root,
        state,
        source_node="synthesis",
        analysis={"directions": [
            {**d, "id": d.get("title"), "markdown_file": ""}
            for d in action.params.get("new_synthesis_directions", []) or []
        ]},
        analysis_dir="",
    )
    # ingest copies target_statement/regularization_target/first_steps and the
    # contract fields from each direction dict itself, so no per-source zip is
    # needed (a zip misaligns when ingest skips an already-known id mid-list).
    for entry in synthesized:
        entry["synthesis"] = True
        entry["rank_decision"] = decision_ref
    write_state(ctx.root, state)
    return {"pool_edits": edits, "new_directions": [e["direction_id"] for e in synthesized]}


def _run_analyze(action: Action, ctx: SupervisionContext) -> dict[str, Any]:
    # NOTE: must NOT set node["analyzed"] here. "analyzed" means "analysis
    # ingested into the pool" — ingest_fresh_analysis fires on
    # has_analysis and not analyzed, so flagging at launch time would
    # permanently skip ingestion once the detached analyze agent finishes.
    # Re-fire while the agent runs is guarded by `not has_analysis` plus
    # the trigger debounce.
    node_project = Path(action.params["project"])
    state = read_state(ctx.root)
    directions = _analysis_direction_count(state)
    result = run_cli(
        [
            "tool", "generalize", "analyze", str(node_project),
            "--directions", str(directions), "--json", *_executor_args(),
        ],
        cwd=ctx.root,
    )
    return {
        "node": action.params.get("node_id"),
        "directions": directions,
        "returncode": result.get("returncode"),
    }


def _ingest_analysis(action: Action, ctx: SupervisionContext) -> dict[str, Any]:
    node_project = Path(action.params["project"])
    analysis = read_json(node_project / "generalize" / "analysis.json", default=None)
    if not isinstance(analysis, dict):
        raise RuntimeError(f"no readable analysis.json in {node_project}")
    state = read_state(ctx.root)
    added = ingest_analysis_directions(
        ctx.root,
        state,
        source_node=str(action.params.get("node_id")),
        analysis=analysis,
        analysis_dir=f"{action.params.get('project_rel', node_project)}/generalize",
    )
    for node in state.get("nodes", []):
        if node.get("node_id") == action.params.get("node_id"):
            node["analyzed"] = True
            # Copy the post-run outcome summary onto the node entry: the
            # intent markdown never changes after proposal, so this is the
            # only family-state record of what the node actually proved
            # (the dashboard and the family digest both read it from here).
            summary = str(analysis.get("result_summary") or "").strip()
            if summary:
                node["result_summary"] = summary
    write_state(ctx.root, state)
    return {"added": [e["direction_id"] for e in added]}


def _family_context_digest(root: Path, *, limit: int = 12) -> str:
    """Compact family intelligence injected into a new child's goal prompt.

    Takes the NEWEST ledger entries: the index is append-ordered, so a
    head-slice would freeze the digest at the family's oldest intelligence
    once the ledger outgrows the limit."""
    lines: list[str] = []
    entries = load_family_index(root)[-limit:]
    if entries:
        lines.append("Verified/known claims across the family (leads — re-verify locally):")
        for entry in entries:
            sightings = ", ".join(
                f"{s.get('project')}: {s.get('status')} ({s.get('assumptions_scope')})"
                for s in entry.get("sightings", [])[:4]
            )
            lines.append(f"- {entry.get('curated_summary')} [{sightings}]")
    failed_path = root / "memory" / "family" / "failed_paths.jsonl"
    if failed_path.exists():
        rows = [l for l in failed_path.read_text(encoding="utf-8", errors="replace").splitlines() if l.strip()]
        if rows:
            lines.append("Known dead ends (do not re-explore without new information):")
            for raw in rows[-limit:]:
                try:
                    row = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                record = row.get("record", {})
                lines.append(
                    f"- [{row.get('source_project')}] {record.get('route', '?')}: {record.get('reason', '?')}"
                )
    return "\n".join(lines)


def _schedule_next(action: Action, ctx: SupervisionContext) -> dict[str, Any]:
    """Seed and start the best approved directions until every free slot is
    filled. Budget caps are enforced HERE, mechanically — judgment outputs
    cannot override them. Filling all slots per tick matters: with one seed
    per tick, workers that finish inside a tick interval leave slots idle
    and effective parallelism degrades to ~1."""
    scheduled: list[dict[str, Any]] = []
    reason = None
    while True:
        state = read_state(ctx.root)
        budget = budget_status(state)
        if budget["exhausted"]:
            reason = "budget exhausted"
            break
        if budget["slots_free"] <= 0:
            reason = "no free slots"
            break
        if budget["nodes"] >= budget["max_nodes"]:
            reason = "max_nodes reached"
            break
        from iteris.evolve import schedulable_directions

        candidates = schedulable_directions(state)
        if not candidates:
            reason = "no approved directions"
            break
        scheduled.append(_seed_direction(ctx, state, candidates[0]))
    result: dict[str, Any] = {
        "scheduled": [item["direction_id"] for item in scheduled] or None,
        "children": [item["child"] for item in scheduled],
    }
    if reason and not scheduled:
        result["reason"] = reason
    return result


def _seed_direction(
    ctx: SupervisionContext, state: dict[str, Any], direction: dict[str, Any]
) -> dict[str, Any]:
    from iteris.generalize import seed_generalization

    source_node = str(direction.get("source_node"))
    parent = ctx.root
    for node in state.get("nodes", []):
        if node.get("node_id") == source_node:
            parent = node_root(ctx.root, node)
            break
    markdown = direction.get("markdown_file")
    if markdown:
        direction_arg = str((ctx.root / str(markdown)).resolve())
    else:
        direction_arg = (
            f"{direction.get('title')}\n\nTarget statement: {direction.get('target_statement', '')}\n"
            f"First steps: {json.dumps(direction.get('first_steps') or [])}"
        )
    target = ctx.root.parent / f"{ctx.root.name}-evo-{slugify(str(direction.get('title') or direction['direction_id']), 32)}"
    contract = {key: direction[key] for key in CONTRACT_FIELDS if direction.get(key)}
    seed = seed_generalization(
        parent,
        source_result=None,
        direction=direction_arg,
        target=target,
        evolve_root=evolve_root_entry(ctx.root),
        family_context=_family_context_digest(ctx.root) or None,
        contract=contract or None,
    )
    run_result = run_cli(["run", str(seed.child_root), "--json", *_executor_args()], cwd=ctx.root)

    state = read_state(ctx.root)
    set_direction_status(
        state, direction["direction_id"], "running", seeded_project=str(seed.child_root)
    )
    state.setdefault("nodes", []).append(
        {
            "project": f"../{seed.child_root.name}",
            "node_id": seed.problem_id,
            "kind": direction.get("kind") or "abstract",
            "parent": parent.name,
            "seeded_from_direction": direction["direction_id"],
            "started_at": now_iso(),
            "last_progress_at": now_iso(),
            "analyzed": False,
        }
    )
    write_state(ctx.root, state)
    return {
        "direction_id": direction["direction_id"],
        "child": str(seed.child_root),
        "run": run_result.get("returncode"),
    }


def _stop_harvest(action: Action, ctx: SupervisionContext) -> dict[str, Any]:
    """L4: stop a node's run, record the boundary, free the slot.
    Harvest needs no copying here — node deltas flow through the next tick's
    NodesSensor -> curate_facts pass before this entry is journaled as done."""
    state = read_state(ctx.root)
    node_id = action.params.get("node_id")
    target_node = None
    for node in state.get("nodes", []):
        if node.get("node_id") == node_id:
            target_node = node
            break
    if target_node is None:
        raise RuntimeError(f"unknown node: {node_id}")
    project = node_root(ctx.root, target_node)
    stop_result = run_cli(["stop", str(project), "--json"], cwd=ctx.root)
    direction_id = target_node.get("seeded_from_direction")
    if direction_id:
        try:
            set_direction_status(state, str(direction_id), "blocked")
        except Exception:
            pass
        record_boundary(
            state,
            direction_id=str(direction_id),
            verdict="blocked",
            reason_summary=str(action.params.get("reason", "stalled")),
        )
    write_state(ctx.root, state)
    return {"stopped": node_id, "stop_returncode": stop_result.get("returncode")}


def _message_node(action: Action, ctx: SupervisionContext) -> dict[str, Any]:
    """Resolve a node id to its project path, then send through the messages
    module — the one sanctioned cross-project write."""
    from iteris.messages import send

    state = read_state(ctx.root)
    for node in state.get("nodes", []):
        if node.get("node_id") == action.params.get("node_id"):
            message = send(
                node_root(ctx.root, node),
                body=str(action.params.get("body", "")),
                priority=str(action.params.get("priority", "high")),
                type="nudge",
                sender="supervisor",
            )
            return {"msg_id": message["msg_id"], "node": node.get("node_id")}
    raise RuntimeError(f"unknown node: {action.params.get('node_id')}")


def _write_stage_report(action: Action, ctx: SupervisionContext) -> dict[str, Any]:
    milestone = slugify(str(action.params.get("milestone") or "milestone"), 40)
    report_dir = ctx.root / "artifacts" / "reports" / f"{now_stamp()}-{milestone}"
    report_dir.mkdir(parents=True, exist_ok=True)
    path = report_dir / "report.md"
    body = str(action.params.get("report_markdown", "")).strip() + "\n"
    headline = str(action.params.get("headline", "")).strip()
    path.write_text(f"# {headline}\n\n{body}" if headline else body, encoding="utf-8")
    return {"path": str(path)}


_TERMINAL_PHASE = {"verified": "goal_success_verified", "reduced": "principled_stop_certified"}


def _mark_direction_verified(action: Action, ctx: SupervisionContext) -> dict[str, Any]:
    """Mechanical terminal-path slot release: a node whose run reached a
    certified terminal flips its direction running -> terminal status
    (``verified`` for goal_success, ``reduced`` for a certified principled_stop).
    Without this the family can never schedule beyond the first max_concurrent
    terminals — and a principled_stop that never released would deadlock it."""
    state = read_state(ctx.root)
    marked: list[str] = []
    # node_id -> the terminal status to stamp on its phase.
    node_status: dict[str, str] = {}
    for item in action.params.get("directions", []):
        direction_id = str(item.get("direction_id"))
        status = str(item.get("status") or "verified")
        if status not in _TERMINAL_PHASE:
            status = "verified"
        try:
            entry = find_direction(state, direction_id)
        except Exception:
            continue
        if entry.get("status") == "running":
            set_direction_status(state, direction_id, status)
            marked.append(direction_id)
            node_status[str(item.get("node_id"))] = status
    reaped: list[str] = []
    if marked:
        for node in state.get("nodes", []):
            status = node_status.get(str(node.get("node_id")))
            if status is None:
                continue
            node["phase"] = _TERMINAL_PHASE[status]
            node["last_progress_at"] = now_iso()
            # The worker run is finalized; reap its idle tmux session so
            # long-running families do not accumulate dozens of them.
            project = node_root(ctx.root, node)
            session = f"iteris-{session_slug(project.name)}"
            if tmux_session_alive(session) and _kill_session(session):
                reaped.append(session)
        write_state(ctx.root, state)
    return {"released": marked, "reaped_sessions": reaped}


def _reap_sessions(action: Action, ctx: SupervisionContext) -> dict[str, Any]:
    """Backstop reaper: finished workers that slipped past the success-path
    reap in ``_mark_direction_verified`` (e.g. verified before that code
    deployed), plus analyze sessions whose analysis.json is already on disk."""
    reaped: list[str] = []
    failed: list[str] = []
    sessions = [
        f"iteris-{session_slug(Path(p).name)}" for p in action.params.get("workers", [])
    ] + list(action.params.get("analyze_sessions", []))
    for session in sessions:
        if not tmux_session_alive(session):
            continue
        (reaped if _kill_session(session) else failed).append(session)
    return {"reaped": reaped, "failed": failed}


def _kill_session(session: str) -> bool:
    try:
        proc = subprocess.run(
            ["tmux", "kill-session", "-t", session],
            capture_output=True,
            timeout=15,
        )
        return proc.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def build_actuators() -> list[CallableActuator]:
    return [
        CallableActuator(name="approve_directions", fn=_approve_directions),
        CallableActuator(name="mark_direction_verified", fn=_mark_direction_verified),
        CallableActuator(name="apply_curation", fn=_apply_curation),
        CallableActuator(name="apply_ranking", fn=_apply_ranking),
        CallableActuator(name="apply_plan_revision", fn=_apply_plan_revision),
        CallableActuator(name="run_analyze", fn=_run_analyze),
        CallableActuator(name="ingest_analysis", fn=_ingest_analysis),
        CallableActuator(name="schedule_next", fn=_schedule_next),
        CallableActuator(name="stop_harvest", fn=_stop_harvest),
        CallableActuator(name="write_stage_report", fn=_write_stage_report),
        CallableActuator(name="message_node", fn=_message_node),
        CallableActuator(name="reap_sessions", fn=_reap_sessions),
        send_message_actuator(),
        CallableActuator(name="record", fn=lambda action, ctx: dict(action.params)),
    ]


# --------------------------------------------------------------------------- #
# Triggers + profile
# --------------------------------------------------------------------------- #


def _nodes(obs: dict[str, Observation]) -> list[dict[str, Any]]:
    return obs["nodes"].data["nodes"]


def _verified_running_pairs(obs: dict[str, Observation]) -> list[dict[str, Any]]:
    """Goal_success-verified nodes whose direction is still marked running.

    Used by the full-success milestone report (which must stay goal_success
    specific). Slot release uses ``_terminal_running_pairs`` instead."""
    running = {
        e.get("direction_id")
        for e in obs["pool"].data["pool"]
        if e.get("status") == "running"
    }
    return [
        {"direction_id": n.get("running_direction"), "node_id": n["node_id"]}
        for n in _nodes(obs)
        if n["verified"] and n.get("running_direction") in running
    ]


def _terminal_running_pairs(obs: dict[str, Observation]) -> list[dict[str, Any]]:
    """Terminal nodes whose direction is still marked running — the slot-release
    transition the mechanical layer has not yet booked.

    Terminal covers BOTH goal_success (-> direction status ``verified``) and a
    certified principled_stop (-> ``reduced``). A principled_stop is a common,
    honest terminal for research-open directions; releasing its slot here is
    what keeps a budget-bounded family from deadlocking once max-concurrent
    children have honestly stopped."""
    running = {
        e.get("direction_id")
        for e in obs["pool"].data["pool"]
        if e.get("status") == "running"
    }
    pairs: list[dict[str, Any]] = []
    for n in _nodes(obs):
        if not n.get("terminal") or n.get("running_direction") not in running:
            continue
        status = "verified" if n.get("verified") else "reduced"
        pairs.append(
            {"direction_id": n.get("running_direction"), "node_id": n["node_id"], "status": status}
        )
    return pairs


def build_triggers() -> list[TriggerRule]:
    return [
        TriggerRule(
            name="approve_lapsed_proposals",
            condition=lambda obs: bool(obs["pool"].data["lapsed_proposals"]),
            response="approve_directions",
            kind="action",
        ),
        TriggerRule(
            name="reap_finished_sessions",
            condition=lambda obs: any(
                (n["terminal"] and n["session_alive"] and not n["new_facts"])
                or (n["has_analysis"] and n.get("analyze_session_alive"))
                for n in _nodes(obs)
            ),
            response="reap_sessions",
            kind="action",
            params=lambda obs: {
                "workers": [
                    n["project"]
                    for n in _nodes(obs)
                    if n["terminal"] and n["session_alive"] and not n["new_facts"]
                ],
                "analyze_sessions": [
                    f"iteris-analyze-{session_slug(Path(n['project']).name)}"
                    for n in _nodes(obs)
                    if n["has_analysis"] and n.get("analyze_session_alive")
                ],
            },
            debounce_ticks=1,
        ),
        TriggerRule(
            name="release_verified_direction",
            condition=lambda obs: bool(_terminal_running_pairs(obs)),
            response="mark_direction_verified",
            kind="action",
            params=lambda obs: {"directions": _terminal_running_pairs(obs)},
        ),
        TriggerRule(
            name="analyze_verified_node",
            condition=lambda obs: any(
                n["verified"] and not n["analyzed"] and not n["has_analysis"] for n in _nodes(obs)
            ),
            response="run_analyze",
            kind="action",
            params=lambda obs: next(
                {
                    "node_id": n["node_id"],
                    "project": n["project"],
                    "project_rel": n["project_rel"],
                }
                for n in _nodes(obs)
                if n["verified"] and not n["analyzed"] and not n["has_analysis"]
            ),
            debounce_ticks=3,
        ),
        TriggerRule(
            name="ingest_fresh_analysis",
            condition=lambda obs: any(
                n["has_analysis"] and not n["analyzed"] for n in _nodes(obs)
            ),
            response="ingest_analysis",
            kind="action",
            params=lambda obs: next(
                {
                    "node_id": n["node_id"],
                    "project": n["project"],
                    "project_rel": n["project_rel"],
                }
                for n in _nodes(obs)
                if n["has_analysis"] and not n["analyzed"]
            ),
        ),
        TriggerRule(
            name="curate_new_facts",
            condition=lambda obs: any(
                n["new_facts"] or n["new_failed_paths"] for n in _nodes(obs)
            ),
            response="curate_facts",
            kind="contract",
            params=lambda obs: {
                "new_facts": [r for n in _nodes(obs) for r in n["new_facts"]],
                "new_failed_paths": [r for n in _nodes(obs) for r in n["new_failed_paths"]],
            },
        ),
        TriggerRule(
            name="rank_unranked_directions",
            condition=lambda obs: bool(obs["pool"].data["unranked_approved"]),
            response="rerank_directions",
            kind="contract",
            params=lambda obs: {"unranked": obs["pool"].data["unranked_approved"]},
            debounce_ticks=1,
        ),
        TriggerRule(
            name="schedule_free_slot",
            condition=lambda obs: (
                obs["budget"].data["slots_free"] > 0
                and not obs["budget"].data["exhausted"]
                and any(
                    e.get("status") == "approved" and e.get("rank") is not None
                    for e in obs["pool"].data["pool"]
                )
            ),
            response="schedule_next",
            kind="action",
        ),
        TriggerRule(
            name="stall_suspected",
            condition=lambda obs: any(
                n["stalled_hours"] is not None
                and n["stalled_hours"] >= n["stall_threshold"]
                and n["running_direction"]
                for n in _nodes(obs)
            ),
            response="diagnose_stall",
            kind="contract",
            params=lambda obs: {
                "stalled_nodes": [
                    n
                    for n in _nodes(obs)
                    if n["stalled_hours"] is not None
                    and n["stalled_hours"] >= n["stall_threshold"]
                ]
            },
            debounce_ticks=4,
        ),
        TriggerRule(
            name="pool_pressure_revise",
            # revise_plan's only other path is the stall flow, which a fast
            # family never enters — without this trigger, global pool
            # consolidation and cross-branch synthesis are dead code.
            condition=lambda obs: (
                len(
                    [
                        e
                        for e in obs["pool"].data["pool"]
                        if e.get("status") in {"proposed", "approved"}
                    ]
                )
                >= 8
            ),
            response="revise_plan",
            kind="contract",
            params=lambda obs: {
                "reason": "pool_pressure",
                # Portfolio stats stated mechanically so the judgment cannot
                # overlook them: a prose-only mandate was ignored in practice.
                "portfolio": {
                    "verified_nodes": sum(1 for n in _nodes(obs) if n["verified"]),
                    "failed_or_blocked_nodes": sum(1 for n in _nodes(obs) if n["blocked"]),
                    "boundary_entries": len(obs["pool"].data.get("boundary", [])),
                },
                "open_directions": [
                    e.get("direction_id")
                    for e in obs["pool"].data["pool"]
                    if e.get("status") in {"proposed", "approved"}
                ],
            },
            debounce_ticks=8,
        ),
        TriggerRule(
            name="milestone_node_verified",
            # Tied to the success transition (verified node, direction still
            # running) rather than to new_verified fact deltas: fact cursors
            # may already be consumed by curation ticks before verification
            # detection, which would silently skip the milestone.
            condition=lambda obs: bool(_verified_running_pairs(obs)),
            response="write_stage_report",
            kind="contract",
            params=lambda obs: {
                "milestone": "node-verified",
                "verified_nodes": [n["node_id"] for n in _nodes(obs) if n["verified"]],
            },
            debounce_ticks=2,
        ),
        TriggerRule(
            name="milestone_budget_half",
            condition=lambda obs: bool(obs["budget"].data.get("crossed_half")),
            response="write_stage_report",
            kind="contract",
            params=lambda obs: {"milestone": "budget-50pct"},
        ),
        TriggerRule(
            name="finale_budget_or_pool_exhausted",
            condition=lambda obs: (
                obs["budget"].data["exhausted"]
                or (
                    obs["pool"].data["open_count"] == 0
                    and len(obs["pool"].data["pool"]) > 0
                )
            ),
            response="write_stage_report",
            kind="contract",
            params=lambda obs: {
                "milestone": "final",
                "reason": "budget exhausted"
                if obs["budget"].data["exhausted"]
                else "direction pool empty",
            },
            debounce_ticks=10,
        ),
    ]


def build_profile(root: Path, *, tick_seconds: int = 1800) -> Profile:
    return Profile(
        name="evolve",
        sensors=[
            NodesSensor(root),
            PoolSensor(root),
            BudgetSensor(root),
            FamilyLedgerSensor(root),
        ],
        triggers=build_triggers(),
        contracts=build_contracts(),
        actuators=build_actuators(),
        tick_seconds=tick_seconds,
    )

"""Evolve profile: hermetic full-cycle tests with canned judgment backends."""

from __future__ import annotations

import json

import pytest

import iteris.supervision.profiles.evolve as evolve_profile
from iteris.evolve import init_state, read_state
from iteris.generalize import seed_generalization
from iteris.memory.facts import rebuild_fact_index, update_fact_metadata, write_fact
from iteris.memory.family import load_family_index
from iteris.messages import list_messages
from iteris.project import init_project, write_json
from iteris.supervision.engine import run_tick
from iteris.supervision.journal import read_entries

ORIGIN = "fact:root:lemma-0:20260101T000000Z"


def _project_with_result(path, fact_prefix):
    init_project(path)
    write_fact(
        path,
        fact_id=f"fact:{fact_prefix}:lemma-0:20260101T000000Z",
        source_task="task-0",
        claim_summary="Verified lemma.",
        statement="Lemma holds.",
        status="verified",
        verification="verify-0",
    )
    rebuild_fact_index(path)
    result = path / "results" / "prob" / "proof.md"
    result.parent.mkdir(parents=True)
    result.write_text("# Proof\n", encoding="utf-8")
    return "results/prob/proof.md"


@pytest.fixture()
def family(tmp_path, monkeypatch):
    root = tmp_path / "root"
    source = _project_with_result(root, "root")
    gen1 = seed_generalization(
        root, source_result=source, direction="Abstract upward.", target=tmp_path / "gen1"
    )
    monkeypatch.setattr(evolve_profile, "tmux_session_alive", lambda name: False)
    cli_calls: list[list[str]] = []
    monkeypatch.setattr(
        evolve_profile, "run_cli", lambda args, cwd: cli_calls.append(args) or {"returncode": 0}
    )
    init_state(
        root,
        goal="push to the most general kernel class",
        policy={"seed_veto_window_minutes": 0},
        budget={"wall_hours": 100.0, "max_concurrent": 1},
    )
    return root, gen1.child_root, cli_calls


def _backend(dispatch):
    def backend(prompt: str) -> str:
        for needle, reply in dispatch.items():
            if needle in prompt:
                return json.dumps(reply)
        raise AssertionError(f"unexpected judgment prompt: {prompt[:120]}")

    return backend


def _analyze_calls(cli_calls):
    return [args for args in cli_calls if args[:3] == ["tool", "generalize", "analyze"]]


CURATE_REPLY = {
    "entries": [
        {
            "origin_fact_id": ORIGIN,
            "claim_summary": "Verified lemma.",
            "curated_summary": "Lemma holds under the root kernel assumptions.",
            "family_relevance": "Load-bearing for all descendants.",
            "substance": "NEW",
            "substance_why": "New lemma, not a restatement.",
            "sightings": [
                {"project": "root", "fact_id": ORIGIN, "status": "verified",
                 "assumptions_scope": "root setting", "verification": "verify-0"}
            ],
        }
    ],
    "failed_paths": [
        {"source_project": "gen1", "record": {"route": "naive bound", "reason": "diverges"}}
    ],
    "skipped": [],
}

STAGE_REPLY = {"headline": "Milestone", "report_markdown": "# Report\n\nSelf-contained."}


def test_first_tick_harvests_into_family_ledger(family):
    root, gen1, _ = family
    profile = evolve_profile.build_profile(root)
    summary = run_tick(
        root,
        profile,
        backend_override=_backend({"curation judgment": CURATE_REPLY, "stage-report": STAGE_REPLY}),
    )
    assert "curate_new_facts" in summary.fired
    index = load_family_index(root)
    assert len(index) == 1 and index[0]["origin_fact_id"] == ORIGIN
    assert (root / "memory" / "family" / "failed_paths.jsonl").exists()

    # Second tick: cursors advanced, no re-curation.
    second = run_tick(root, profile, backend_override=_backend({"stage-report": STAGE_REPLY}))
    assert "curate_new_facts" not in second.fired


def test_analysis_ingest_rank_and_schedule(family, tmp_path):
    root, gen1, cli_calls = family
    profile = evolve_profile.build_profile(root)
    # Quiet the initial harvest.
    run_tick(root, profile, backend_override=_backend(
        {"curation judgment": CURATE_REPLY, "stage-report": STAGE_REPLY}))

    # Mark gen1 verified (with a real result artifact) and give it an analysis
    # the master should ingest.
    gen1_result = gen1 / "results" / "prob" / "proof.md"
    gen1_result.parent.mkdir(parents=True, exist_ok=True)
    gen1_result.write_text("# Gen1 proof\n", encoding="utf-8")
    (gen1 / "STATUS.md").write_text(
        "phase: goal_success_verified\n"
        "target_artifact: results/prob/proof.md\n"
        "last_updated: 2026-06-10T00:00:00Z\n",
        encoding="utf-8",
    )
    direction_md = gen1 / "generalize" / "auto-01-go-deeper.md"
    direction_md.parent.mkdir(parents=True, exist_ok=True)
    direction_md.write_text("# Go deeper\n\n## Target statement\n\nDeeper.\n", encoding="utf-8")
    write_json(
        gen1 / "generalize" / "analysis.json",
        {"directions": [
            {"id": "01", "title": "Go deeper", "kind": "abstract", "uses_inputs": ["STP"],
             "scores": {"impact": "H"}, "tier": 1,
             "markdown_file": "generalize/auto-01-go-deeper.md"},
        ]},
    )

    tick = run_tick(root, profile, backend_override=_backend({"stage-report": STAGE_REPLY}))
    assert "ingest_fresh_analysis" in tick.fired
    state = read_state(root)
    pool = state["direction_pool"]
    assert len(pool) == 1 and pool[0]["status"] == "proposed"

    # Window 0 -> approve fires this tick; ranking judgment on the next
    # (detect happens before act within a tick).
    rank_reply = {"ranking": [pool[0]["direction_id"]], "drops": [], "overlap_notes": "single"}
    tick = run_tick(
        root, profile,
        backend_override=_backend({"stage-report": STAGE_REPLY}),
    )
    assert "approve_lapsed_proposals" in tick.fired
    tick = run_tick(
        root, profile,
        backend_override=_backend({"direction-ranking": rank_reply, "stage-report": STAGE_REPLY}),
    )
    assert "rank_unranked_directions" in tick.fired
    state = read_state(root)
    entry = state["direction_pool"][0]
    assert entry["status"] == "approved" and entry["rank"] == 1
    assert entry["rank_decision"], "ranking must cite its journal decision"

    # Next tick: slot free -> schedule_next seeds a real child and starts the run.
    tick = run_tick(root, profile, backend_override=_backend({"stage-report": STAGE_REPLY}))
    assert "schedule_free_slot" in tick.fired
    assert {"action": "schedule_next", "ok": True} in tick.actions
    state = read_state(root)
    entry = state["direction_pool"][0]
    assert entry["status"] == "running"
    child = next(p for p in root.parent.iterdir() if p.name.startswith("root-evo-"))
    lineage = json.loads((child / ".iteris" / "generalize.json").read_text(encoding="utf-8"))
    assert lineage["evolve_root"]["node_id"] == "root"
    prompt = (child / ".iteris" / "goal_prompt.txt").read_text(encoding="utf-8")
    assert "Family intelligence" in prompt and "message ack" in prompt.replace("`", "")
    # The child run carries an explicit --executor: the master's env
    # pins the backend; default resolves to codex.
    assert ["run", str(child), "--json", "--executor", "codex"] in cli_calls
    assert any(n["node_id"] == child.name.lower().replace("_", "-") or n["project"] == f"../{child.name}"
               for n in state["nodes"])

    # Budget cap is mechanical: with the slot occupied nothing else schedules.
    tick = run_tick(root, profile, backend_override=_backend({"stage-report": STAGE_REPLY}))
    assert "schedule_free_slot" not in tick.fired


def test_stall_diagnosis_message_and_stop(family):
    root, gen1, cli_calls = family
    profile = evolve_profile.build_profile(root)
    run_tick(root, profile, backend_override=_backend(
        {"curation judgment": CURATE_REPLY, "stage-report": STAGE_REPLY}))

    state = read_state(root)
    state["direction_pool"].append(
        {"direction_id": "dir-x", "source_node": "root", "status": "running",
         "kind": "abstract", "uses_inputs": [], "scores": {}, "rank": 1,
         "proposed_at": "2026-06-01T00:00:00Z", "vetoable_until": None}
    )
    for node in state["nodes"]:
        node["seeded_from_direction"] = "dir-x"
        node["started_at"] = "2026-06-01T00:00:00Z"
        node["last_progress_at"] = "2026-06-01T00:00:00Z"
    from iteris.evolve import write_state

    write_state(root, state)

    gen1_node_id = state["nodes"][0]["node_id"]
    diagnose_message = {
        "stalled": True, "node_id": gen1_node_id,
        "diagnosis": "no verified facts in 200h", "recommendation": "message",
        "message_text": "Try the family's Fekete envelope lead.",
    }
    tick = run_tick(
        root, profile,
        backend_override=_backend({"stall-diagnosis": diagnose_message, "stage-report": STAGE_REPLY}),
    )
    assert "stall_suspected" in tick.fired
    unread = list_messages(gen1, unread_only=True)
    assert unread and unread[0]["priority"] == "high"

    # Clear debounce, then a stop_harvest verdict records the boundary.
    cursors = root / ".iteris" / "supervision" / "cursors.json"
    data = json.loads(cursors.read_text(encoding="utf-8"))
    data = {k: v for k, v in data.items() if not k.startswith("debounce:")}
    cursors.write_text(json.dumps(data), encoding="utf-8")

    diagnose_stop = {
        "stalled": True, "node_id": gen1_node_id,
        "diagnosis": "exhausted route", "recommendation": "stop_harvest",
        "boundary_reason": "STP fails for this kernel; route exhausted",
    }
    tick = run_tick(
        root, profile,
        backend_override=_backend({"stall-diagnosis": diagnose_stop, "stage-report": STAGE_REPLY}),
    )
    state = read_state(root)
    assert state["boundary"] and state["boundary"][0]["verdict"] == "blocked"
    assert any(args[0] == "stop" for args in cli_calls)
    assert next(e for e in state["direction_pool"] if e["direction_id"] == "dir-x")["status"] == "blocked"


def test_stage_report_is_immutable_artifact(family):
    root, _, _ = family
    profile = evolve_profile.build_profile(root)
    run_tick(root, profile, backend_override=_backend(
        {"curation judgment": CURATE_REPLY, "stage-report": STAGE_REPLY}))
    reports = list((root / "artifacts" / "reports").glob("*/report.md"))
    if reports:  # milestone fired on adoption tick
        text = reports[0].read_text(encoding="utf-8")
        assert "Self-contained" in text


def test_dry_run_has_zero_side_effects(family):
    root, gen1, cli_calls = family
    profile = evolve_profile.build_profile(root)
    summary = run_tick(
        root, profile, dry_run=True,
        backend_override=_backend({"curation judgment": CURATE_REPLY, "stage-report": STAGE_REPLY}),
    )
    assert not summary.idle
    assert load_family_index(root) == []
    assert cli_calls == []
    intents = [e for e in read_entries(root) if e["entry_type"] == "action_intent"]
    assert intents and all(e["payload"]["dry_run"] for e in intents)


def test_verified_node_releases_slot_and_fires_milestone(family):
    root, gen1, cli_calls = family
    profile = evolve_profile.build_profile(root)
    # Quiet the initial harvest tick.
    run_tick(root, profile, backend_override=_backend(
        {"curation judgment": CURATE_REPLY, "stage-report": STAGE_REPLY}))

    # Wire gen1 as a running direction's node, the way schedule_next records it.
    state = read_state(root)
    state["direction_pool"].append(
        {"direction_id": "dir-x", "source_node": "root", "title": "X",
         "kind": "abstract", "status": "running", "rank": 1}
    )
    gen1_node = next(n for n in state["nodes"] if n["project"].endswith(gen1.name))
    gen1_node["seeded_from_direction"] = "dir-x"
    from iteris.evolve import write_state
    write_state(root, state)

    # The worker finishes with a free-form phase ("complete", NOT
    # goal_success_verified) and a passing goal_success verification.
    result = gen1 / "results" / "prob" / "proof.md"
    result.parent.mkdir(parents=True, exist_ok=True)
    result.write_text("# Gen1 proof\n", encoding="utf-8")
    (gen1 / "STATUS.md").write_text("phase: complete\nnext: finalized\n", encoding="utf-8")
    verdict_dir = gen1 / "verification" / "results"
    verdict_dir.mkdir(parents=True, exist_ok=True)
    write_json(
        verdict_dir / "verify-1.json",
        {"request_id": "verify-1", "mode": "goal_success", "passed": True,
         "target_artifact": "results/prob/proof.md"},
    )

    from iteris.evolve import budget_status
    assert budget_status(read_state(root))["slots_free"] == 0

    tick = run_tick(root, profile, backend_override=_backend({"stage-report": STAGE_REPLY}))
    # Mechanical detection despite the non-contract phase string.
    assert "release_verified_direction" in tick.fired
    # The success milestone rides the same transition.
    assert "milestone_node_verified" in tick.fired
    state = read_state(root)
    entry = next(e for e in state["direction_pool"] if e["direction_id"] == "dir-x")
    assert entry["status"] == "verified"
    assert budget_status(state)["slots_free"] == 1
    # Pool regrowth: the verified node gets an analyze run.
    analyze_calls = _analyze_calls(cli_calls)
    assert analyze_calls
    assert analyze_calls[-1][analyze_calls[-1].index("--directions") + 1] == "3"

    # Idempotent: nothing re-fires once the transition is booked.
    second = run_tick(root, profile, backend_override=_backend({"stage-report": STAGE_REPLY}))
    assert "release_verified_direction" not in second.fired


def test_principled_stop_node_releases_slot_without_deadlock(family):
    # A child that terminates via a certified principled_stop (honest stop, not
    # goal_success) must free its concurrency slot just like goal_success, or a
    # family of principled_stop children deadlocks. The direction lands in the
    # distinct terminal status 'reduced' (not 'verified'), and the full-success
    # milestone does NOT fire.
    root, gen1, cli_calls = family
    profile = evolve_profile.build_profile(root)
    run_tick(root, profile, backend_override=_backend(
        {"curation judgment": CURATE_REPLY, "stage-report": STAGE_REPLY}))

    state = read_state(root)
    state["direction_pool"].append(
        {"direction_id": "dir-ps", "source_node": "root", "title": "PS",
         "kind": "abstract", "status": "running", "rank": 1}
    )
    gen1_node = next(n for n in state["nodes"] if n["project"].endswith(gen1.name))
    gen1_node["seeded_from_direction"] = "dir-ps"
    from iteris.evolve import write_state, budget_status
    write_state(root, state)

    # Certified principled_stop terminal: a passing principled_stop verification
    # whose reduced target artifact exists; the STATUS phase is non-contract.
    reduced = gen1 / "results" / "prob" / "answer_reduced_verified.md"
    reduced.parent.mkdir(parents=True, exist_ok=True)
    reduced.write_text("# Reduced / certified obstruction\n", encoding="utf-8")
    (gen1 / "STATUS.md").write_text("phase: stopped\nnext: principled stop\n", encoding="utf-8")
    verdict_dir = gen1 / "verification" / "results"
    verdict_dir.mkdir(parents=True, exist_ok=True)
    write_json(
        verdict_dir / "verify-ps.json",
        {"request_id": "verify-ps", "mode": "principled_stop", "passed": True,
         "target_artifact": "results/prob/answer_reduced_verified.md"},
    )

    assert budget_status(read_state(root))["slots_free"] == 0  # slot consumed

    tick = run_tick(root, profile, backend_override=_backend({"stage-report": STAGE_REPLY}))
    assert "release_verified_direction" in tick.fired
    # A principled_stop is NOT a full-success milestone.
    assert "milestone_node_verified" not in tick.fired
    state = read_state(root)
    entry = next(e for e in state["direction_pool"] if e["direction_id"] == "dir-ps")
    assert entry["status"] == "reduced"            # distinct terminal, not 'verified'
    assert budget_status(state)["slots_free"] == 1  # slot freed -> no deadlock

    # Restart recovery: even if the direction is somehow still 'running' (e.g.
    # adopt_family_nodes re-scan on a master restart left it so), the per-tick
    # terminal detection re-releases it rather than re-adopting as running.
    entry["status"] = "running"
    gen1_node = next(n for n in state["nodes"] if n["project"].endswith(gen1.name))
    gen1_node["phase"] = None
    write_state(root, state)
    restart = run_tick(root, profile, backend_override=_backend({"stage-report": STAGE_REPLY}))
    assert "release_verified_direction" in restart.fired
    assert next(e for e in read_state(root)["direction_pool"] if e["direction_id"] == "dir-ps")["status"] == "reduced"


def test_detached_analysis_is_ingested_after_it_lands(family):
    root, gen1, cli_calls = family
    profile = evolve_profile.build_profile(root)
    run_tick(root, profile, backend_override=_backend(
        {"curation judgment": CURATE_REPLY, "stage-report": STAGE_REPLY}))

    state = read_state(root)
    state["direction_pool"].append(
        {"direction_id": "dir-x", "source_node": "root", "title": "X",
         "kind": "abstract", "status": "running", "rank": 1}
    )
    state["policy"]["analysis_directions_per_node"] = 2
    gen1_node = next(n for n in state["nodes"] if n["project"].endswith(gen1.name))
    gen1_node["seeded_from_direction"] = "dir-x"
    from iteris.evolve import write_state
    write_state(root, state)

    result = gen1 / "results" / "prob" / "proof.md"
    result.parent.mkdir(parents=True, exist_ok=True)
    result.write_text("# Gen1 proof\n", encoding="utf-8")
    (gen1 / "STATUS.md").write_text("phase: complete\n", encoding="utf-8")
    verdict_dir = gen1 / "verification" / "results"
    verdict_dir.mkdir(parents=True, exist_ok=True)
    write_json(
        verdict_dir / "verify-1.json",
        {"request_id": "verify-1", "mode": "goal_success", "passed": True,
         "target_artifact": "results/prob/proof.md"},
    )

    tick = run_tick(root, profile, backend_override=_backend({"stage-report": STAGE_REPLY}))
    assert "analyze_verified_node" in tick.fired
    analyze_calls = _analyze_calls(cli_calls)
    assert analyze_calls[-1][analyze_calls[-1].index("--directions") + 1] == "2"
    # Launching the detached analyze must NOT mark the node analyzed —
    # analyzed means "ingested", and flagging here would skip ingestion.
    state = read_state(root)
    gen1_node = next(n for n in state["nodes"] if n["project"].endswith(gen1.name))
    assert gen1_node["analyzed"] is False

    # The detached analyze agent lands its analysis later.
    direction_md = gen1 / "generalize" / "auto-01-next.md"
    direction_md.parent.mkdir(parents=True, exist_ok=True)
    direction_md.write_text("# Next\n\n## Target statement\n\nNext.\n", encoding="utf-8")
    write_json(
        gen1 / "generalize" / "analysis.json",
        {"directions": [
            {"id": "01", "title": "Next", "kind": "abstract", "uses_inputs": ["STP"],
             "scores": {"impact": "H"}, "tier": 1,
             "markdown_file": "generalize/auto-01-next.md"},
        ]},
    )
    tick = run_tick(root, profile, backend_override=_backend({"stage-report": STAGE_REPLY}))
    assert "ingest_fresh_analysis" in tick.fired
    state = read_state(root)
    assert any(e.get("source_node") == gen1_node["node_id"] for e in state["direction_pool"])
    gen1_node = next(n for n in state["nodes"] if n["project"].endswith(gen1.name))
    assert gen1_node["analyzed"] is True


def test_schedule_fills_all_free_slots_in_one_tick(family, tmp_path):
    root, gen1, cli_calls = family
    (root / "STATUS.md").write_text(
        "phase: goal_success_verified\ntarget_artifact: results/prob/proof.md\n",
        encoding="utf-8",
    )
    # Two slots, two ranked approved directions -> one tick seeds both.
    state = read_state(root)
    state["budget"]["max_concurrent"] = 2
    for i, title in enumerate(["First direction", "Second direction"], start=1):
        md = root / "generalize" / f"auto-0{i}.md"
        md.parent.mkdir(parents=True, exist_ok=True)
        md.write_text(f"# {title}\n\n## Target statement\n\nT.\n", encoding="utf-8")
        state["direction_pool"].append(
            {"direction_id": f"dir-fill-{i}", "source_node": "root", "title": title,
             "kind": "abstract", "status": "approved", "rank": i,
             "markdown_file": f"generalize/auto-0{i}.md"}
        )
    from iteris.evolve import write_state
    write_state(root, state)

    profile = evolve_profile.build_profile(root)
    tick = run_tick(root, profile, backend_override=_backend(
        {"curation judgment": CURATE_REPLY, "stage-report": STAGE_REPLY}))
    assert "schedule_free_slot" in tick.fired
    state = read_state(root)
    statuses = {e["direction_id"]: e["status"] for e in state["direction_pool"]}
    assert statuses["dir-fill-1"] == "running" and statuses["dir-fill-2"] == "running"
    from iteris.evolve import budget_status
    assert budget_status(state)["slots_free"] == 0


def test_pool_pressure_triggers_global_revise(family):
    root, gen1, _ = family
    state = read_state(root)
    for i in range(8):
        state["direction_pool"].append(
            {"direction_id": f"dir-crowd-{i}", "source_node": "root", "title": f"D{i}",
             "kind": "abstract", "status": "approved", "rank": None}
        )
    from iteris.evolve import write_state
    write_state(root, state)

    revise_reply = {
        "pool_edits": [{"direction_id": "dir-crowd-7", "status": "superseded",
                        "why": "duplicate genre"}],
        "new_synthesis_directions": [],
        "narrative": "consolidated",
    }
    profile = evolve_profile.build_profile(root)
    tick = run_tick(root, profile, backend_override=_backend(
        {"curation judgment": CURATE_REPLY, "stage-report": STAGE_REPLY,
         "plan-revision": revise_reply,
         "direction-ranking": {"ranking": [f"dir-crowd-{i}" for i in range(7)],
                                "drops": [], "overlap_notes": "ok"}}))
    assert "pool_pressure_revise" in tick.fired
    state = read_state(root)
    statuses = {e["direction_id"]: e["status"] for e in state["direction_pool"]}
    assert statuses["dir-crowd-7"] == "superseded"


def test_curate_boundary_entries_land_in_evolve_state(family):
    root, gen1, _ = family
    profile = evolve_profile.build_profile(root)
    reply = {
        **CURATE_REPLY,
        "boundary_entries": [
            {
                "source": "gen1",
                "verdict": "impossible",
                "reason_summary": "axis projection provably loses sign-regularity",
                "evidence": [ORIGIN],
            }
        ],
    }
    summary = run_tick(
        root,
        profile,
        backend_override=_backend({"curation judgment": reply, "stage-report": STAGE_REPLY}),
    )
    assert "curate_new_facts" in summary.fired
    boundary = read_state(root)["boundary"]
    assert len(boundary) == 1
    assert boundary[0]["verdict"] == "impossible"
    assert boundary[0]["evidence"] == [ORIGIN]


def test_curate_validator_rejects_missing_substance_and_bad_boundary():
    base_entry = CURATE_REPLY["entries"][0]
    ungraded = {**CURATE_REPLY, "entries": [{k: v for k, v in base_entry.items() if k != "substance"}]}
    errors = evolve_profile._validate_curate(ungraded)
    assert any("substance" in e for e in errors)
    bad_boundary = {
        **CURATE_REPLY,
        "boundary_entries": [{"source": "", "verdict": "meh", "reason_summary": "", "evidence": []}],
    }
    errors = evolve_profile._validate_curate(bad_boundary)
    for needle in ("source", "verdict", "reason_summary", "evidence"):
        assert any(needle in e for e in errors), (needle, errors)


def test_revise_validator_requires_novelty_claim_and_contract_shape():
    decision = {
        "pool_edits": [],
        "new_synthesis_directions": [
            {"title": "T", "kind": "abstract", "target_statement": "S"}
        ],
    }
    errors = evolve_profile._validate_revise(decision)
    assert any("novelty_claim" in e for e in errors)
    decision["new_synthesis_directions"][0].update(
        {"novelty_claim": "uses the Brownian counterexample", "success_criteria": []}
    )
    errors = evolve_profile._validate_revise(decision)
    assert any("success_criteria" in e for e in errors)
    decision["new_synthesis_directions"][0]["success_criteria"] = ["a real criterion"]
    assert evolve_profile._validate_revise(decision) == []


def test_scheduled_child_carries_success_contract(family):
    root, gen1, cli_calls = family
    profile = evolve_profile.build_profile(root)
    run_tick(root, profile, backend_override=_backend(
        {"curation judgment": CURATE_REPLY, "stage-report": STAGE_REPLY}))

    # Seeding resolves the parent's source result from STATUS.md.
    (root / "STATUS.md").write_text(
        "phase: goal_success_verified\ntarget_artifact: results/prob/proof.md\n",
        encoding="utf-8",
    )

    # Put an approved+ranked direction with a success contract in the pool.
    state = read_state(root)
    state["direction_pool"].append(
        {
            "direction_id": "dir-human-hard-push",
            "source_node": "root",
            "title": "Hard push",
            "markdown_file": None,
            "kind": "abstract",
            "uses_inputs": [],
            "scores": {},
            "tier": 1,
            "status": "approved",
            "rank": 1,
            "rank_decision": "d-1",
            "proposed_at": "2026-06-12T00:00:00Z",
            "vetoable_until": None,
            "target_statement": "the genuinely non-product theorem",
            "first_steps": ["state the theorem"],
            "success_criteria": ["prove the non-product case for d >= 2"],
            "does_not_count": ["the tensorization warm-up"],
            "audit_routes": ["R1: perturbative product", "R2: fiber transfer"],
        }
    )
    from iteris.evolve import write_state

    write_state(root, state)

    tick = run_tick(root, profile, backend_override=_backend({"stage-report": STAGE_REPLY}))
    assert "schedule_free_slot" in tick.fired
    child = next(p for p in root.parent.iterdir() if p.name.startswith("root-evo-"))
    seeded = (child / "sources" / "generalization-direction.md").read_text(encoding="utf-8")
    assert "## Success criteria" in seeded and "non-product case" in seeded
    assert "## What does NOT count" in seeded
    assert "## Audit routes" in seeded and "R2: fiber transfer" in seeded
    lineage = json.loads((child / ".iteris" / "generalize.json").read_text(encoding="utf-8"))
    assert "AUDIT contract" in lineage["goal"]
    assert "[NEW]" in lineage["goal"]


def test_curate_boundary_entries_are_deduped_across_ticks(family):
    root, gen1, _ = family
    profile = evolve_profile.build_profile(root)
    boundary_reply = {
        **CURATE_REPLY,
        "boundary_entries": [
            {
                "source": "gen1",
                "verdict": "impossible",
                "reason_summary": "axis projection loses sign-regularity",
                "evidence": [ORIGIN],
            }
        ],
    }
    run_tick(
        root,
        profile,
        backend_override=_backend({"curation judgment": boundary_reply, "stage-report": STAGE_REPLY}),
    )
    assert len(read_state(root)["boundary"]) == 1

    # New fact appears -> curate fires again and re-emits the same boundary
    # entry; the actuator must not append a duplicate.
    write_fact(
        gen1,
        fact_id="fact:gen1:lemma-9:20260101T000000Z",
        source_task="task-9",
        claim_summary="Another lemma.",
        statement="Holds.",
        status="verified",
        verification="verify-9",
    )
    rebuild_fact_index(gen1)
    second = run_tick(
        root,
        profile,
        backend_override=_backend({"curation judgment": boundary_reply, "stage-report": STAGE_REPLY}),
    )
    assert "curate_new_facts" in second.fired
    assert len(read_state(root)["boundary"]) == 1

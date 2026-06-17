"""Evolve state: node adoption, direction pool lifecycle, budget."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from iteris.evolve import (
    EvolveError,
    approve_lapsed,
    budget_status,
    evolve_root_entry,
    family_member_dirs,
    ingest_analysis_directions,
    init_state,
    propose_direction,
    read_state,
    schedulable_directions,
    set_direction_status,
    unseeded_open,
    veto_direction,
    write_state,
)
from iteris.generalize import seed_generalization
from iteris.memory.facts import rebuild_fact_index, update_fact_metadata, write_fact
from iteris.project import init_project


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
def family(tmp_path):
    root = tmp_path / "root"
    source = _project_with_result(root, "root")
    gen1 = seed_generalization(
        root, source_result=source, direction="Abstract upward.", target=tmp_path / "gen1"
    )
    inherited = gen1.inherited[0]["child_fact_id"]
    update_fact_metadata(
        gen1.child_root, fact_id=inherited, status="verified",
        verification="verify-c", review_level="verified",
    )
    rebuild_fact_index(gen1.child_root)
    child_result = gen1.child_root / "results" / "prob" / "proof.md"
    child_result.parent.mkdir(parents=True)
    child_result.write_text("# Proof\n", encoding="utf-8")
    gen2 = seed_generalization(
        gen1.child_root, source_result="results/prob/proof.md",
        direction="Instantiate to the bosonic kernel.", target=tmp_path / "gen2",
    )
    unrelated = tmp_path / "other"
    _project_with_result(unrelated, "other")
    return root, gen1.child_root, gen2.child_root


def _analysis(n=3):
    kinds = ["abstract", "instantiate", "abstract"]
    return {
        "directions": [
            {
                "id": f"0{i+1}",
                "title": f"Direction {i+1}",
                "kind": kinds[i % 3],
                "uses_inputs": ["STP"],
                "scores": {"impact": "H"},
                "tier": 1 + i,
                "markdown_file": f"generalize/auto-0{i+1}-slug.md",
            }
            for i in range(n)
        ]
    }


def test_family_membership_via_parent_chain(family):
    root, gen1, gen2 = family
    members = [p.name for p in family_member_dirs(root)]
    assert members == ["gen1", "gen2"]  # grandchild found via chain, outsider excluded


def test_init_adopts_nodes_and_rejects_double_init(family):
    root, gen1, gen2 = family
    state = init_state(root, goal="push to the most general kernel class")
    assert [n["node_id"] for n in state["nodes"]] == ["gen1", "gen2"]
    assert state["nodes"][1]["kind"] == "instantiate"  # from direction title
    assert state["policy"]["analysis_directions_per_node"] == 3
    with pytest.raises(EvolveError):
        init_state(root, goal="again")
    assert read_state(root)["goal"].startswith("push")
    assert evolve_root_entry(root)["node_id"] == "root"


def test_pool_lifecycle_veto_and_approval(family):
    root, _, _ = family
    state = init_state(root, goal="g", policy={"seed_veto_window_minutes": 60})
    added = ingest_analysis_directions(
        root, state, source_node="root", analysis=_analysis(), analysis_dir="../root/generalize"
    )
    assert len(added) == 3 and all(e["status"] == "proposed" for e in added)
    # Re-ingest is idempotent.
    assert ingest_analysis_directions(
        root, state, source_node="root", analysis=_analysis(), analysis_dir="../root/generalize"
    ) == []
    write_state(root, state)

    # Inside the window nothing approves; after it everything does.
    assert approve_lapsed(state, now=datetime.now(timezone.utc)) == []
    later = datetime.now(timezone.utc) + timedelta(minutes=61)
    approved = approve_lapsed(state, now=later)
    assert len(approved) == 3
    write_state(root, state)

    target = added[0]["direction_id"]
    entry = veto_direction(root, target)
    assert entry["status"] == "vetoed"
    state = read_state(root)
    with pytest.raises(EvolveError):
        set_direction_status(state, target, "seeded")  # human veto is final
    with pytest.raises(EvolveError):
        veto_direction(root, added[1]["direction_id"] + "-nope")

    # Abstract bias: remaining approved sort abstract-first, then tier.
    order = [e["direction_id"] for e in schedulable_directions(state)]
    kinds = [find_kind(state, d) for d in order]
    assert kinds == ["abstract", "instantiate"]

    # Explicit rank from a rerank decision wins over heuristics.
    set_direction_status(state, order[-1], "approved")
    find(state, order[-1])["rank"] = 1
    assert schedulable_directions(state)[0]["direction_id"] == order[-1]


def find(state, direction_id):
    from iteris.evolve import find_direction

    return find_direction(state, direction_id)


def find_kind(state, direction_id):
    return find(state, direction_id).get("kind")


def test_ingest_carries_success_contract_fields(family):
    root, _, _ = family
    state = init_state(root, goal="g")
    analysis = _analysis(1)
    analysis["directions"][0].update(
        {
            "success_criteria": ["prove the non-product case"],
            "does_not_count": ["the tensorization warm-up"],
            "audit_routes": ["R1", "R2"],
            "experiment_gate": "run the scan first",
            "novelty_claim": "uses the coupled-frequency kernel",
        }
    )
    added = ingest_analysis_directions(
        root, state, source_node="root", analysis=analysis, analysis_dir="g"
    )
    entry = added[0]
    assert entry["success_criteria"] == ["prove the non-product case"]
    assert entry["does_not_count"] == ["the tensorization warm-up"]
    assert entry["audit_routes"] == ["R1", "R2"]
    assert entry["experiment_gate"] == "run the scan first"
    assert entry["novelty_claim"] == "uses the coupled-frequency kernel"


def test_propose_direction_is_first_class_and_unseeded_surfaces(family, tmp_path):
    root, _, _ = family
    init_state(root, goal="g")
    md = tmp_path / "06b-non-product-multidim.md"
    md.write_text(
        "# Non-product multidimensional kernels\n\n"
        "## Success criteria\n\n- a genuinely non-product theorem\n",
        encoding="utf-8",
    )
    entry = propose_direction(root, markdown=md, rank=1, approve=True)
    assert entry["human_injected"] is True
    assert entry["status"] == "approved" and entry["rank"] == 1
    assert entry["title"] == "Non-product multidimensional kernels"
    # Authored contract sections are parsed onto the entry so seeding adds
    # the goal-level contract clauses for human directions too.
    assert entry["success_criteria"] == ["a genuinely non-product theorem"]
    # Outside-the-root file was copied to a root-relative path.
    assert (root / entry["markdown_file"]).is_file()
    state = read_state(root)
    assert schedulable_directions(state)[0]["direction_id"] == entry["direction_id"]
    assert unseeded_open(state) == [entry["direction_id"]]
    # Idempotence: same file cannot enter the pool twice.
    with pytest.raises(EvolveError):
        propose_direction(root, markdown=md)
    # Default entry path: proposed, no veto window (the human IS the vetoer).
    md2 = tmp_path / "05b-zolotarev.md"
    md2.write_text("# Zolotarev classification\n", encoding="utf-8")
    entry2 = propose_direction(root, markdown=md2)
    assert entry2["status"] == "proposed" and entry2["vetoable_until"] is None
    assert len(approve_lapsed(read_state(root))) == 1  # lapses immediately
    with pytest.raises(EvolveError):
        propose_direction(root, markdown=tmp_path / "missing.md")
    with pytest.raises(EvolveError):
        propose_direction(root, markdown=md2, kind="weird")


def test_budget_status_slots_and_exhaustion(family):
    root, _, _ = family
    state = init_state(root, goal="g", budget={"wall_hours": 10.0, "max_concurrent": 2})
    ingest_analysis_directions(
        root, state, source_node="root", analysis=_analysis(2), analysis_dir="x"
    )
    for entry in state["direction_pool"]:
        entry["status"] = "running"
    status = budget_status(state)
    assert status["running"] == 2 and status["slots_free"] == 0 and not status["exhausted"]

    state["run"]["started_at"] = (
        (datetime.now(timezone.utc) - timedelta(hours=11)).isoformat().replace("+00:00", "Z")
    )
    status = budget_status(state)
    assert status["exhausted"] and status["remaining_hours"] == 0.0


def test_contract_from_markdown_parses_bullets_prose_and_gate():
    from iteris.evolve import contract_from_markdown

    text = (
        "# Direction\n\n"
        "## Success criteria\n\n- criterion A\n- criterion B\n\n"
        "## What does NOT count\n\nProse only: the warm-up result.\n\n"
        "## Audit routes\n\n- R1\n- R2\n\n"
        "## Experiment gate\n\nRun the scan first.\nArchive the data.\n\n"
        "## Risks\n\n- unrelated section is ignored\n"
    )
    contract = contract_from_markdown(text)
    assert contract["success_criteria"] == ["criterion A", "criterion B"]
    assert contract["does_not_count"] == ["Prose only: the warm-up result."]
    assert contract["audit_routes"] == ["R1", "R2"]
    assert contract["experiment_gate"] == "Run the scan first.\nArchive the data."
    assert "risks" not in contract and len(contract) == 4
    assert contract_from_markdown("# Plain\n\nNo contract sections.\n") == {}


def test_veto_why_is_recorded(family):
    root, _, _ = family
    state = init_state(root, goal="g", policy={"seed_veto_window_minutes": 60})
    added = ingest_analysis_directions(
        root, state, source_node="root", analysis=_analysis(1), analysis_dir="g"
    )
    write_state(root, state)
    entry = veto_direction(root, added[0]["direction_id"], why="certificate genre, third repeat")
    assert entry["vetoed_why"] == "certificate genre, third repeat"


def test_direction_ids_disambiguate_same_prefix_source_nodes(family):
    from iteris.evolve import _direction_id

    # Real collision from the GECP run: sibling probe-drop-* nodes share a
    # 24-char prefix, so their analyses' numeric direction ids ("01") deduped
    # against each other and fresh directions were silently never ingested.
    a = _direction_id("iteris-gecp-evo-probe-drop-same-kernel-compat", {"id": "01"})
    b = _direction_id("iteris-gecp-evo-probe-drop-robust-compactness-an", {"id": "01"})
    assert a != b

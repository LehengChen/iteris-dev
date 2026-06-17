"""Tests for monitor lookups."""

from __future__ import annotations

import json

from iteris.guide.lookups import default_lookups, lookup_doctor, lookup_status
from iteris.evolve import evolve_root_entry, write_state
from iteris.memory.family import record_failed_path, upsert_family_entries
from iteris.memory.facts import rebuild_fact_index, write_fact
from iteris.project import init_project, read_json, write_json
from iteris.frontier import set_active_frontier
from iteris.tasks import upsert_pool_task


def test_lookup_doctor_outside_project(tmp_path):
    payload = lookup_doctor(None)
    assert payload["lookup"] == "doctor"
    assert "environment" in payload


def test_default_lookups_single_project(tmp_path):
    src = tmp_path / "p.tex"
    src.write_text("problem", encoding="utf-8")
    root = tmp_path / "proj"
    init_project(root, source=src)
    lookups = default_lookups(root)
    assert "doctor" in lookups
    assert "status" in lookups
    assert lookups["status"]["lookup"] == "status"


def test_lookup_status_fields(tmp_path):
    src = tmp_path / "p.tex"
    src.write_text("problem", encoding="utf-8")
    root = tmp_path / "proj"
    init_project(root, source=src)
    status = lookup_status(root)
    assert status["project_path"] == str(root.resolve())
    assert "target_artifact" in status


def test_lookup_status_includes_single_project_math_progress(tmp_path):
    src = tmp_path / "p.tex"
    src.write_text("Prove a compact trace inequality.", encoding="utf-8")
    root = tmp_path / "proj"
    init_project(root, source=src)
    target = "results/proj/answer.md"
    (root / "STATUS.md").write_text(
        "phase: proof_search\n"
        f"target_artifact: {target}\n"
        "summary: trace inequality reduced to a boundary lemma\n",
        encoding="utf-8",
    )
    (root / target).parent.mkdir(parents=True, exist_ok=True)
    (root / target).write_text("# Answer\n\nA partial reduction has been found.\n", encoding="utf-8")
    write_fact(
        root,
        fact_id="fact:proj:boundary-lemma",
        source_task="task-proof",
        claim_summary="The main trace estimate follows from the boundary lemma.",
        statement="If the boundary lemma holds, the compact trace inequality follows.",
        status="verified",
        fact_type="reduction",
        verification="verify-1",
        review_level="agent",
    )
    write_fact(
        root,
        fact_id="fact:proj:endpoint-blocker",
        source_task="task-endpoint",
        claim_summary="Endpoint interpolation still loses the needed compactness.",
        statement="The current endpoint interpolation route loses compactness.",
        status="reviewed",
        fact_type="blocker",
        verification="verify-2",
        review_level="agent",
    )
    rebuild_fact_index(root)
    upsert_pool_task(
        root,
        task_id="task-boundary",
        mode="proof",
        objective="Prove the remaining boundary lemma.",
        status="ready",
        priority=10,
    )
    upsert_pool_task(
        root,
        task_id="task-endpoint",
        mode="proof",
        objective="Resolve or bypass the endpoint compactness blocker.",
        status="blocked",
        priority=5,
    )
    set_active_frontier(
        root,
        frontier_id="frontier-boundary",
        title="Boundary lemma route",
        summary="Only the boundary lemma remains open.",
        tasks=["task-boundary"],
        facts=["fact:proj:boundary-lemma"],
        gaps=["complete proof of the lemma"],
    )
    frontier_path = root / "memory" / "facts" / "FRONTIER_INDEX.json"
    frontier = read_json(frontier_path)
    frontier["active_frontiers"][0].update(
        {
            "status": "blocked",
            "blocker_fact_ids": ["fact:proj:endpoint-blocker"],
            "blocked_tasks": ["task-endpoint"],
            "active_tasks": ["task-boundary"],
            "open_questions": ["Can endpoint compactness be recovered by strengthening the trace space?"],
            "next_actions": ["Audit the endpoint interpolation loss."],
        }
    )
    write_json(frontier_path, frontier)

    payload = lookup_status(root)
    progress = payload["math_progress"]
    assert progress["target_artifact"] == target
    assert progress["target_exists"] is True
    assert "partial reduction" in progress["target_excerpt"]
    assert progress["facts"]["total"] == 2
    assert progress["facts"]["by_status"]["verified"] == 1
    assert "trace estimate" in progress["facts"]["recent_verified_or_reviewed"][0]["claim_summary"]
    assert progress["tasks"]["by_status"]["ready"] == 1
    assert progress["tasks"]["by_status"]["blocked"] == 1
    assert progress["frontier"]["active"][0]["frontier_id"] == "frontier-boundary"
    assert progress["frontier"]["active"][0]["tasks"] == ["task-boundary"]
    assert progress["frontier"]["active"][0]["facts"] == ["fact:proj:boundary-lemma"]
    assert progress["frontier"]["active"][0]["blocker_fact_ids"] == ["fact:proj:endpoint-blocker"]
    assert progress["frontier"]["active"][0]["blocked_tasks"] == ["task-endpoint"]
    assert progress["blockers"]["blocked_tasks"][0]["task_id"] == "task-endpoint"
    assert progress["blockers"]["blocker_facts"][0]["fact_id"] == "fact:proj:endpoint-blocker"


def test_evolve_lookup_is_summarized(tmp_path):
    src = tmp_path / "p.tex"
    src.write_text("problem", encoding="utf-8")
    root = tmp_path / "family"
    init_project(root, source=src)
    write_state(
        root,
        {
            "schema_version": "iteris.evolve_state.v0",
            "goal": "test evolve goal",
            "budget": {"wall_hours": 10, "spent_hours": 1, "max_concurrent": 2, "max_nodes": 10},
            "run": {},
            "nodes": [
                {
                    "node_id": f"node-{i}",
                    "project": f"../node-{i}",
                    "kind": "abstract",
                    "phase": "goal_success_verified" if i % 2 else "running",
                    "seeded_from_direction": f"dir-{i}",
                    "last_progress_at": f"2026-06-17T00:{i:02d}:00Z",
                    "analyzed": True,
                }
                for i in range(30)
            ],
            "direction_pool": [
                {
                    "direction_id": f"dir-{i}",
                    "title": "Long direction title " + ("x" * 500),
                    "status": "approved" if i % 2 else "verified",
                    "tier": "high",
                    "rank": i,
                    "markdown_file": f"generalize/dir-{i}.md",
                    "seeded_project": str(tmp_path / f"node-{i}"),
                    "superseded_why": "y" * 1000,
                }
                for i in range(80)
            ],
            "boundary": [
                {"direction_id": f"dir-{i}", "verdict": "blocked", "reason_summary": "z" * 500}
                for i in range(20)
            ],
        },
    )
    upsert_family_entries(
        root,
        [
            {
                "origin_fact_id": "fact:node-a:main-claim",
                "claim_summary": "A new kernel envelope lemma was verified.",
                "curated_summary": "Verified mathematical progress on a reusable kernel envelope lemma.",
                "family_relevance": "Useful for sibling generalizations.",
                "substance": "NEW",
                "sightings": [{"project": "node-a", "fact_id": "fact-a", "status": "verified"}],
            }
        ],
    )
    record_failed_path(
        root,
        source_project="node-b",
        record={"route": "Try unrestricted rank transfer", "reason": "Boundary obstruction is verified."},
    )

    lookups = default_lookups(root)
    evolve = lookups["evolve_status"]
    assert evolve["direction_pool"]["total"] == 80
    assert evolve["nodes"]["total"] == 30
    assert evolve["boundary"]["total"] == 20
    assert isinstance(evolve["direction_pool"]["recent"], list)
    assert len(evolve["direction_pool"]["recent"]) == 12
    assert "superseded_why" not in evolve["direction_pool"]["recent"][0]
    assert "state_file" in evolve
    assert evolve["math_progress"]["family_claims_total"] == 1
    assert evolve["math_progress"]["substance_counts"]["NEW"] == 1
    assert "kernel envelope" in evolve["math_progress"]["recent_claims"][0]["claim_summary"]
    assert evolve["math_progress"]["failed_paths_total"] == 1
    assert "unrestricted rank" in evolve["math_progress"]["recent_failed_paths"][0]["route"]
    assert len(json.dumps(evolve, ensure_ascii=False)) < 20000


def test_family_child_lookup_focuses_current_node(tmp_path):
    src = tmp_path / "p.tex"
    src.write_text("problem", encoding="utf-8")
    root = tmp_path / "family"
    child = tmp_path / "child"
    init_project(root, source=src)
    init_project(child, source=src)
    write_json(
        child / ".iteris" / "generalize.json",
        {
            "schema_version": "iteris.generalize_lineage.v0",
            "evolve_root": evolve_root_entry(root),
            "direction": {"title": "Curved boundary perturbation"},
            "inherited_facts": [{"child_fact_id": "fact:child:inherited", "claim_summary": "Inherited lemma."}],
        },
    )
    write_state(
        root,
        {
            "schema_version": "iteris.evolve_state.v0",
            "goal": "family goal",
            "budget": {"wall_hours": 10, "spent_hours": 1, "max_concurrent": 2, "max_nodes": 10},
            "run": {},
            "nodes": [
                {
                    "node_id": "node-child",
                    "project": "../child",
                    "kind": "generalization",
                    "phase": "running",
                    "seeded_from_direction": "dir-child",
                    "last_progress_at": "2026-06-17T00:00:00Z",
                }
            ],
            "direction_pool": [
                {
                    "direction_id": "dir-child",
                    "title": "Curved boundary perturbation",
                    "status": "running",
                    "tier": "high",
                    "seeded_project": "../child",
                }
            ],
            "boundary": [],
        },
    )
    write_json(
        child / "generalize" / "analysis.json",
        {"schema_version": "iteris.generalize_analysis.v0", "result_summary": "Commutator route reduced to one estimate."},
    )

    lookups = default_lookups(child)
    assert "evolve_status" in lookups
    assert lookups["evolve_status"]["current_child"]["nodes"][0]["node_id"] == "node-child"
    assert "Commutator route" in lookups["evolve_status"]["current_child"]["nodes"][0]["result_summary"]
    assert lookups["evolve_status"]["current_child"]["directions"][0]["direction_id"] == "dir-child"
    assert lookups["status"]["math_progress"]["generalization"]["evolve_root"]["path"] == str(root.resolve())

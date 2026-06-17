from __future__ import annotations

import json

from iteris.memory.facts import rebuild_fact_index, validate_project_facts, write_fact
from iteris.memory.search import search_memory
from iteris.project import init_project
from iteris.bootstrap import run_once
from iteris.tasks import list_tasks
from iteris.verification.local import latest_results, verify_local


SOURCE = r"""
\begin{problem}
Let T be a bounded linear operator on a normed space.
Prove a sharper stability estimate for T by exploiting an explicitly stated
structural decomposition.
\end{problem}
"""


def test_init_run_memory_and_verification(tmp_path):
    source = tmp_path / "problem.tex"
    source.write_text(SOURCE, encoding="utf-8")
    project = tmp_path / "iteris-test"

    init_result = init_project(project, source=source)
    assert (project / "iteris.toml").exists()
    assert (project / "sources" / "problem.tex").exists()
    assert init_result["project_id"] == "iteris-test"

    run_result = run_once(project)
    assert run_result["verification_verdict"] == "accepted"

    validation = validate_project_facts(project)
    assert validation["ok"] is True
    assert validation["count"] == 1
    assert validation["rebuilt"] == 1

    facts = (project / "memory" / "facts" / "FACT_INDEX.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(facts) == 1
    fact_row = json.loads(facts[0])
    assert "source problem" in fact_row["claim_summary"]
    assert fact_row["predecessors"] == []

    results = search_memory(project, "source problem recorded", limit=5)
    assert results
    assert any(row.get("fact_id") == run_result["fact_id"] for row in results)

    verification_results = latest_results(project)
    assert len(verification_results) == 1
    assert verification_results[0]["verdict"] == "accepted"


def test_verifier_accepts_existing_external_artifact(tmp_path):
    project = tmp_path / "project"
    init_project(project)
    artifact = tmp_path / "external-note.md"
    artifact.write_text("# External note\n\nThis artifact exists.", encoding="utf-8")

    result = verify_local(
        project,
        mode="source",
        claim="The verifier can record an existing project-external artifact.",
        artifacts=[artifact],
    )

    assert result["verdict"] == "accepted"
    assert result["passed"] is True
    assert result["strict_verdict"] == "correct"
    assert result["checked_artifacts"] == [str(artifact.resolve())]


def test_fact_and_assembly_verification_gate(tmp_path):
    project = tmp_path / "project"
    init_project(project)
    fact_id = "fact:project:structural-lemma"
    fact_path = write_fact(
        project,
        fact_id=fact_id,
        source_task="task-proof",
        claim_summary="Structural lemma verified for the final assembly.",
        statement="The structural lemma used in the final assembly is established.",
        status="verified",
        fact_type="proof_step",
        verification="verify-fact-001",
        claim_policy="final_assembly",
    )
    rebuild_fact_index(project)

    target = project / "results" / "problem-001" / "answer_verified.md"
    target.parent.mkdir(parents=True)
    target.write_text(
        "# Verified Answer\n\n"
        "## Fact Index\n\n"
        f"- {fact_id}: structural lemma, verified by verify-fact-001.\n\n"
        "## Assembly\n\n"
        f"The answer follows from {fact_id}.\n",
        encoding="utf-8",
    )

    fact_result = verify_local(project, mode="fact", claim="Verify structural lemma fact.", artifacts=[fact_path.relative_to(project)])
    assert fact_result["passed"] is True
    assert fact_result["claim_ceiling_after_verification"] == "verified"

    assembly_result = verify_local(
        project,
        mode="assembly",
        claim="Verify final answer assembly.",
        artifacts=[target.relative_to(project)],
        target_artifact=target.relative_to(project),
    )
    assert assembly_result["passed"] is True
    assert assembly_result["strict_verdict"] == "correct"
    assert assembly_result["checked_fact_ids"] == [fact_id]


def test_assembly_verification_rejects_unverified_fact(tmp_path):
    project = tmp_path / "project"
    init_project(project)
    fact_id = "fact:project:unverified-claim"
    write_fact(
        project,
        fact_id=fact_id,
        source_task="task-draft",
        claim_summary="Unverified claim.",
        statement="This claim has not been verified.",
        status="submitted",
        verification=None,
    )
    rebuild_fact_index(project)
    target = project / "results" / "problem-001" / "answer_verified.md"
    target.parent.mkdir(parents=True)
    target.write_text(
        "## Fact Index\n\n"
        f"- {fact_id}\n\n"
        "## Assembly\n\n"
        f"The answer follows from {fact_id}.\n",
        encoding="utf-8",
    )

    result = verify_local(project, mode="assembly", claim="Verify final answer assembly.", artifacts=[target.relative_to(project)])
    assert result["passed"] is False
    assert result["strict_verdict"] == "wrong"
    assert result["verdict"] == "needs_repair"
    assert any("not 'verified'" in item["issue"] for item in result["gaps"])


def test_goal_success_rejects_partial_terminal_artifact(tmp_path):
    project = tmp_path / "project"
    init_project(project)
    fact_id = "fact:project:partial-positive"
    write_fact(
        project,
        fact_id=fact_id,
        source_task="task-proof",
        claim_summary="A verified partial positive result.",
        statement="A partial positive result is established.",
        status="verified",
        verification="verify-fact-partial",
    )
    rebuild_fact_index(project)
    target = project / "results" / "problem-001" / "answer_verified.md"
    target.parent.mkdir(parents=True)
    target.write_text(
        "# Verified Answer\n\n"
        "## Goal Summary\n\n"
        "Solve the original problem end-to-end.\n\n"
        "answer_type: verified_partial_positive_result\n\n"
        "## Fact Index\n\n"
        f"- {fact_id}: partial result, verified by verify-fact-partial.\n\n"
        "## Assembly\n\n"
        f"The partial result follows from {fact_id}. A remaining bridge is still open.\n",
        encoding="utf-8",
    )

    assembly = verify_local(project, mode="assembly", claim="Verify internal assembly.", artifacts=[target.relative_to(project)])
    goal_success = verify_local(
        project,
        mode="goal_success",
        claim="Solve the original problem end-to-end.",
        artifacts=[target.relative_to(project)],
        target_artifact=target.relative_to(project),
    )

    assert assembly["passed"] is True
    assert goal_success["passed"] is False
    assert goal_success["verdict"] == "needs_repair"
    assert any("partial" in item["issue"] for item in goal_success["gaps"])


def test_repeated_runs_append_distinct_state(tmp_path):
    source = tmp_path / "problem.tex"
    source.write_text(SOURCE, encoding="utf-8")
    project = tmp_path / "iteris-repeat"
    init_project(project, source=source)

    first = run_once(project)
    second = run_once(project)

    assert first["run_id"] != second["run_id"]
    assert first["fact_id"] != second["fact_id"]
    assert len(list_tasks(project)) == 2

    validation = validate_project_facts(project)
    assert validation["ok"] is True
    assert validation["count"] == 2

    assert len(latest_results(project)) == 2

from __future__ import annotations

import json

from typer.testing import CliRunner

from iteris.cli import app
from iteris.commands.goal import build_goal_prompt, resolve_goal_defaults
from iteris.generalize import seed_generalization
from iteris.memory.facts import fact_files, rebuild_fact_index, validate_fact_file, write_fact
from iteris.project import init_project


def _make_parent(tmp_path, *, name: str = "parent", n_verified: int = 2):
    """Build a parent Iteris project with a verified result and verified facts."""
    parent = tmp_path / name
    init_project(parent)
    for i in range(n_verified):
        write_fact(
            parent,
            fact_id=f"fact:{name}:lemma-{i}:20260101T000000Z",
            source_task=f"task-{i}",
            claim_summary=f"Verified lemma {i} for the parent result.",
            statement=f"Lemma {i} holds in the parent setting.",
            status="verified",
            fact_type="theorem",
            verification=f"verify-{i}",
            claim_policy="proved_from_verified_inputs",
            review_level="verified",
        )
    # A non-verified fact that must NOT be inherited by default.
    write_fact(
        parent,
        fact_id=f"fact:{name}:draft-claim:20260101T000000Z",
        source_task="task-draft",
        claim_summary="A draft claim that should not be inherited.",
        statement="An unverified draft claim.",
        status="submitted",
    )
    rebuild_fact_index(parent)
    result = parent / "results" / "prob" / "proof.md"
    result.parent.mkdir(parents=True)
    result.write_text("# Parent Proof\n\nThe parent theorem is proved.\n", encoding="utf-8")
    return parent, "results/prob/proof.md"


def test_curated_verified_facts_block_is_the_default(tmp_path):
    parent, source_result = _make_parent(tmp_path, name="curated", n_verified=3)
    # Curate a 1-fact core chain in the parent STATUS.md.
    core_id = "fact:curated:lemma-1:20260101T000000Z"
    (parent / "STATUS.md").write_text(
        "phase: goal_success_verified\n"
        "verified_facts:\n"
        f"  - {core_id}\n"
        "last_updated: 2026-01-01T00:00:00Z\n",
        encoding="utf-8",
    )

    result = seed_generalization(
        parent,
        source_result=source_result,
        direction="generalize",
        selected_fact_ids=None,  # default => curated chain
    )

    assert len(result.inherited) == 1
    assert result.inherited[0]["parent_fact_id"] == core_id


def test_seed_from_direction_file(tmp_path):
    parent, source_result = _make_parent(tmp_path)
    direction = parent / "direction.md"
    direction.write_text(
        "# Generalize to TP kernels\n\nAbstract the proof to totally positive kernels.\n",
        encoding="utf-8",
    )

    result = seed_generalization(
        parent,
        source_result=source_result,
        direction=str(direction),
        selected_fact_ids=None,
    )

    child = result.child_root
    assert (child / "iteris.toml").exists()
    assert (child / "sources" / "direction.md").exists()
    # Origin banner was prepended to the copied direction file.
    seeded = (child / "sources" / "direction.md").read_text(encoding="utf-8")
    assert "copied from" in seeded
    lineage = json.loads((child / ".iteris" / "generalize.json").read_text(encoding="utf-8"))
    assert lineage["direction"]["kind"] == "file"
    assert lineage["source_result"] == source_result
    assert lineage["parent_project"]["name"] == parent.name


def test_seed_from_free_text(tmp_path):
    parent, source_result = _make_parent(tmp_path)

    result = seed_generalization(
        parent,
        source_result=source_result,
        direction="abstract the result to general sign-regular kernels",
        selected_fact_ids=None,
    )

    child = result.child_root
    seed_file = child / "sources" / "generalization-direction.md"
    assert seed_file.exists()
    assert "sign-regular" in seed_file.read_text(encoding="utf-8")
    lineage = json.loads((child / ".iteris" / "generalize.json").read_text(encoding="utf-8"))
    assert lineage["direction"]["kind"] == "text"


def test_inherited_facts_are_reviewed_with_predecessors(tmp_path):
    parent, source_result = _make_parent(tmp_path, n_verified=2)

    result = seed_generalization(
        parent,
        source_result=source_result,
        direction="generalize",
        selected_fact_ids=None,
    )
    child = result.child_root

    # Only the 2 verified facts were inherited (the submitted draft was skipped).
    assert len(result.inherited) == 2
    metas = [validate_fact_file(p)["meta"] for p in fact_files(child)]
    assert len(metas) == 2
    for meta in metas:
        assert meta["status"] == "reviewed"
        assert len(meta["predecessors"]) == 1
        assert meta["predecessors"][0].startswith(f"fact:{parent.name}:")

    # The index was rebuilt to match.
    index_lines = (child / "memory" / "facts" / "FACT_INDEX.jsonl").read_text(encoding="utf-8").splitlines()
    assert len([line for line in index_lines if line.strip()]) == 2


def test_selecting_a_subset_of_facts(tmp_path):
    parent, source_result = _make_parent(tmp_path, n_verified=3)
    chosen = [f"fact:{parent.name}:lemma-0:20260101T000000Z"]

    result = seed_generalization(
        parent,
        source_result=source_result,
        direction="generalize",
        selected_fact_ids=chosen,
    )

    assert len(result.inherited) == 1
    assert result.inherited[0]["parent_fact_id"] == chosen[0]


def test_status_target_artifact_is_resolvable(tmp_path):
    parent, source_result = _make_parent(tmp_path)

    result = seed_generalization(
        parent,
        source_result=source_result,
        direction="generalize",
        selected_fact_ids=None,
    )
    child = result.child_root

    status = (child / "STATUS.md").read_text(encoding="utf-8")
    assert f"target_artifact: {result.target_artifact}" in status
    # `iteris run` would resolve the same artifact from STATUS.md.
    _, resolved = resolve_goal_defaults(child)
    assert resolved == result.target_artifact


def test_goal_prompt_contains_generalization_block(tmp_path):
    parent, source_result = _make_parent(tmp_path)

    result = seed_generalization(
        parent,
        source_result=source_result,
        direction="generalize",
        selected_fact_ids=None,
    )
    prompt = (result.child_root / ".iteris" / "goal_prompt.txt").read_text(encoding="utf-8")
    assert "Generalization context:" in prompt
    assert source_result in prompt
    assert result.inherited[0]["child_fact_id"] in prompt
    # The standard contract is still present.
    assert "Iteris goal contract:" in prompt


def test_cli_json_skips_interaction_and_inherits_all(tmp_path):
    parent, source_result = _make_parent(tmp_path)
    direction = parent / "direction.md"
    direction.write_text("# Direction\n\nGeneralize.\n", encoding="utf-8")
    target = tmp_path / "child-out"

    result = CliRunner().invoke(
        app,
        [
            "generalize",
            str(parent),
            "--source-result",
            source_result,
            "--direction",
            str(direction),
            "--target",
            str(target),
            "--json",
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["schema_version"] == "iteris.generalize.v0"
    assert payload["child_project"] == str(target)
    assert len(payload["inherited_facts"]) == 2


def test_errors_on_non_iteris_parent(tmp_path):
    bare = tmp_path / "bare"
    bare.mkdir()

    result = CliRunner().invoke(
        app,
        ["generalize", str(bare), "--direction", "generalize", "--json"],
    )
    assert result.exit_code != 0


def test_source_result_defaults_to_verified_copy_not_working_answer(tmp_path):
    from iteris.generalize import resolve_source_result

    parent = tmp_path / "parent"
    init_project(parent)
    results = parent / "results" / "prob"
    results.mkdir(parents=True)
    # Working answer (exists mid-run regardless of verdict) and the verified copy
    # finalize emits only after goal-success passes.
    (results / "answer.md").write_text("# working answer\n", encoding="utf-8")
    (results / "answer_verified.md").write_text("<!-- ITERIS VERIFIED -->\n# answer\n", encoding="utf-8")
    (parent / "STATUS.md").write_text("target_artifact: results/prob/answer.md\n", encoding="utf-8")

    # Omitted --source-result must resolve to the verified copy, never answer.md.
    assert resolve_source_result(parent, None) == "results/prob/answer_verified.md"


def test_source_result_falls_back_to_recorded_target_when_no_verified_copy(tmp_path):
    from iteris.generalize import resolve_source_result

    parent = tmp_path / "parent"
    init_project(parent)
    results = parent / "results" / "prob"
    results.mkdir(parents=True)
    (results / "answer.md").write_text("# working answer\n", encoding="utf-8")
    (parent / "STATUS.md").write_text("target_artifact: results/prob/answer.md\n", encoding="utf-8")

    # No verified copy yet -> fall back to the recorded target (explicit
    # --source-result remains the operator's escape hatch).
    assert resolve_source_result(parent, None) == "results/prob/answer.md"


def test_errors_on_missing_source_result(tmp_path):
    parent, _ = _make_parent(tmp_path)

    result = CliRunner().invoke(
        app,
        [
            "generalize",
            str(parent),
            "--source-result",
            "results/does-not-exist.md",
            "--direction",
            "generalize",
            "--json",
        ],
    )
    assert result.exit_code != 0


def test_errors_on_existing_nonempty_target(tmp_path):
    parent, source_result = _make_parent(tmp_path)
    target = tmp_path / "occupied"
    target.mkdir()
    (target / "stuff.txt").write_text("busy", encoding="utf-8")

    result = CliRunner().invoke(
        app,
        [
            "generalize",
            str(parent),
            "--source-result",
            source_result,
            "--direction",
            "generalize",
            "--target",
            str(target),
            "--json",
        ],
    )
    assert result.exit_code != 0


def test_build_goal_prompt_generalization_additive():
    base = build_goal_prompt("Solve it.", target_artifact="results/x/answer.md", problem_id="x")
    assert "Generalization context:" not in base

    enriched = build_goal_prompt(
        "Solve it.",
        target_artifact="results/x/answer.md",
        problem_id="x",
        generalization={
            "parent_name": "parent",
            "source_result": "results/prob/proof.md",
            "direction_title": "Generalize",
            "direction_sources_file": "direction.md",
            "inherited_facts": [{"child_fact_id": "fact:x:inherited:lemma", "claim_summary": "L"}],
        },
    )
    assert "Generalization context:" in enriched
    assert "results/prob/proof.md" in enriched
    assert "fact:x:inherited:lemma" in enriched
    # Everything after the block matches the base contract body.
    assert "Iteris goal contract:" in enriched


def test_evolve_membership_guidance_without_family_digest():
    # First children of a new family are seeded before any family intelligence
    # exists; message/search guidance must not be gated on the digest.
    prompt = build_goal_prompt(
        "Solve it.",
        target_artifact="results/x/answer.md",
        problem_id="x",
        generalization={
            "parent_name": "parent",
            "source_result": "results/prob/proof.md",
            "direction_title": "Generalize",
            "direction_sources_file": "direction.md",
            "inherited_facts": [],
            "evolve_root": {"path": "/tmp/root", "node_id": "root"},
        },
    )
    assert "iteris tool message list" in prompt
    assert "scope: family" in prompt
    assert "Family intelligence" not in prompt  # no digest, no digest block


def test_generalization_prompt_context_rebuilds_from_lineage(tmp_path):
    parent, source_result = _make_parent(tmp_path)
    result = seed_generalization(
        parent,
        source_result=source_result,
        direction="generalize",
        selected_fact_ids=None,
        evolve_root={"path": str(parent), "node_id": "root"},
        family_context="Verified across the family: lemma L (assumptions: TP kernel).",
    )
    from iteris.generalize import generalization_prompt_context

    context = generalization_prompt_context(result.child_root)
    assert context is not None
    assert context["parent_name"] == parent.name
    assert context["source_result"] == source_result
    assert context["inherited_facts"][0]["child_fact_id"] == result.inherited[0]["child_fact_id"]
    assert context["evolve_root"]["node_id"] == "root"
    assert "assumptions: TP kernel" in context["family_context"]
    assert context["goal"]

    # Rebuilding the prompt from lineage keeps the generalization block —
    # this is what `iteris run` does when it rewrites goal_prompt.txt.
    prompt = build_goal_prompt(
        context["goal"],
        target_artifact="results/x/answer.md",
        problem_id="x",
        generalization=context,
    )
    assert "Generalization context:" in prompt
    assert "Family intelligence" in prompt
    assert "iteris tool message list" in prompt


def test_long_free_text_direction_does_not_stat_explode(tmp_path):
    # Synthesis directions arrive as multi-line free text whose first path
    # component can exceed NAME_MAX; classify_direction must not let
    # ENAMETOOLONG escape from the file probe.
    parent, source_result = _make_parent(tmp_path)
    long_text = (
        "Robust Witness Assembly Theorem\n\nTarget statement: "
        + "Any kernel with a verified same-kernel CPNG certificate " * 10
    )
    from iteris.generalize import classify_direction

    spec = classify_direction(parent, long_text)
    assert spec.kind == "text"
    assert spec.title == "Robust Witness Assembly Theorem"

    # Single-line but component-too-long input is also free text, not an error.
    spec2 = classify_direction(parent, "x" * 600)
    assert spec2.kind == "text"


def test_seed_with_contract_renders_sections_and_strengthens_goal(tmp_path):
    parent, source_result = _make_parent(tmp_path)
    contract = {
        "success_criteria": ["Prove the bound for every d >= 2."],
        "does_not_count": ["The product-kernel tensorization warm-up."],
        "audit_routes": [
            "R1: perturbative product",
            {"route": "R2: fiber transfer", "hint": "needs the seminorm lemma"},
        ],
        "experiment_gate": "Run the pivot-growth scan and archive script+data first.",
    }

    result = seed_generalization(
        parent,
        source_result=source_result,
        direction="push the theorem to d dimensions",
        selected_fact_ids=None,
        contract=contract,
    )

    seeded = (result.child_root / "sources" / "generalization-direction.md").read_text(
        encoding="utf-8"
    )
    # The verifier reads sources/<direction>.md, so the contract must be IN it.
    assert "## Success criteria" in seeded and "every d >= 2" in seeded
    assert "## What does NOT count" in seeded and "warm-up" in seeded
    assert "## Audit routes" in seeded
    assert "R2: fiber transfer — needs the seminorm lemma" in seeded
    assert "## Experiment gate" in seeded

    goal = result.goal
    assert "Success criteria" in goal
    assert "AUDIT contract" in goal
    assert "EXPERIMENT GATE" in goal
    assert "[NEW]" in goal and "[STD]" in goal and "[MAP]" in goal

    # The seeded goal_prompt.txt carries the strengthened goal verbatim.
    prompt = (result.child_root / ".iteris" / "goal_prompt.txt").read_text(encoding="utf-8")
    assert "What does NOT count" in prompt


def test_contract_sections_defer_to_authored_headings(tmp_path):
    parent, source_result = _make_parent(tmp_path)
    direction = parent / "direction.md"
    direction.write_text(
        "# Hard push\n\n## Success criteria\n\n- the authored criterion\n",
        encoding="utf-8",
    )

    result = seed_generalization(
        parent,
        source_result=source_result,
        direction=str(direction),
        selected_fact_ids=None,
        contract={
            "success_criteria": ["a machine criterion that must NOT be duplicated"],
            "does_not_count": ["the warm-up"],
        },
    )

    seeded = (result.child_root / "sources" / "direction.md").read_text(encoding="utf-8")
    # The authored section wins; the missing one is appended.
    assert seeded.count("## Success criteria") == 1
    assert "machine criterion" not in seeded
    assert "## What does NOT count" in seeded

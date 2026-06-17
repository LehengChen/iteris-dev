"""Phase 0 plumbing: origin_fact_id identity + evolve_root lineage propagation."""

from __future__ import annotations

import json

from iteris.generalize import read_evolve_root, seed_generalization
from iteris.memory.facts import (
    rebuild_fact_index,
    resolve_origin_fact_id,
    update_fact_metadata,
    validate_fact_file,
    write_fact,
)
from iteris.project import init_project


def _make_parent(tmp_path, *, name: str = "parent"):
    parent = tmp_path / name
    init_project(parent)
    write_fact(
        parent,
        fact_id=f"fact:{name}:lemma-0:20260101T000000Z",
        source_task="task-0",
        claim_summary="Verified lemma 0 for the parent result.",
        statement="Lemma 0 holds in the parent setting.",
        status="verified",
        fact_type="theorem",
        verification="verify-0",
        claim_policy="proved_from_verified_inputs",
        review_level="verified",
    )
    rebuild_fact_index(parent)
    result = parent / "results" / "prob" / "proof.md"
    result.parent.mkdir(parents=True)
    result.write_text("# Parent Proof\n\nProved.\n", encoding="utf-8")
    return parent, "results/prob/proof.md"


def _index_rows(project_root):
    index = project_root / "memory" / "facts" / "FACT_INDEX.jsonl"
    return [json.loads(line) for line in index.read_text(encoding="utf-8").splitlines()]


def _seed_child(parent, source_result, *, target, evolve_root=None):
    return seed_generalization(
        parent,
        source_result=source_result,
        direction="Generalize upward.",
        target=target,
        selected_fact_ids=None,
        evolve_root=evolve_root,
    )


def _verify_inherited_and_add_result(child_root, inherited_fact_id):
    """Promote the inherited fact to verified and give the child a result file."""
    update_fact_metadata(
        child_root,
        fact_id=inherited_fact_id,
        status="verified",
        verification="verify-child",
        review_level="verified",
    )
    rebuild_fact_index(child_root)
    result = child_root / "results" / "prob" / "proof.md"
    result.parent.mkdir(parents=True, exist_ok=True)
    result.write_text("# Child Proof\n\nProved.\n", encoding="utf-8")
    return "results/prob/proof.md"


def test_legacy_facts_without_origin_validate_and_resolve_to_self(tmp_path):
    parent, _ = _make_parent(tmp_path)
    rows = _index_rows(parent)
    assert rows, "index should not be empty"
    for row in rows:
        assert row["origin_fact_id"] == row["fact_id"]
    # Frontmatter has no origin_fact_id line, and validation is green.
    fact_path = next((parent / "memory" / "facts").glob("fact-*.md"))
    assert "origin_fact_id" not in fact_path.read_text(encoding="utf-8")
    result = validate_fact_file(fact_path)
    assert result["ok"], result["errors"]
    assert resolve_origin_fact_id(result["meta"]) == result["meta"]["fact_id"]


def test_invalid_origin_fact_id_is_rejected(tmp_path):
    parent, _ = _make_parent(tmp_path)
    fact_path = next((parent / "memory" / "facts").glob("fact-*.md"))
    text = fact_path.read_text(encoding="utf-8")
    fact_path.write_text(
        text.replace("---\nfact_id:", "---\norigin_fact_id: bogus\nfact_id:", 1),
        encoding="utf-8",
    )
    result = validate_fact_file(fact_path)
    assert not result["ok"]
    assert any("origin_fact_id" in err for err in result["errors"])


def test_origin_preserved_across_three_generations(tmp_path):
    parent, source_result = _make_parent(tmp_path, name="root")
    origin_id = "fact:root:lemma-0:20260101T000000Z"

    gen1 = _seed_child(parent, source_result, target=tmp_path / "gen1")
    assert len(gen1.inherited) == 1
    assert gen1.inherited[0]["origin_fact_id"] == origin_id

    gen1_fact_id = gen1.inherited[0]["child_fact_id"]
    gen1_rows = {row["fact_id"]: row for row in _index_rows(gen1.child_root)}
    assert gen1_rows[gen1_fact_id]["origin_fact_id"] == origin_id

    child_source = _verify_inherited_and_add_result(gen1.child_root, gen1_fact_id)
    gen2 = _seed_child(gen1.child_root, child_source, target=tmp_path / "gen2")
    assert len(gen2.inherited) == 1
    # Grandchild's origin is still the ROOT id, not gen1's inherited id.
    assert gen2.inherited[0]["origin_fact_id"] == origin_id
    assert gen2.inherited[0]["parent_fact_id"] == gen1_fact_id

    gen2_rows = {row["fact_id"]: row for row in _index_rows(gen2.child_root)}
    assert gen2_rows[gen2.inherited[0]["child_fact_id"]]["origin_fact_id"] == origin_id

    # Lineage file records the origin too.
    lineage = json.loads(
        (gen2.child_root / ".iteris" / "generalize.json").read_text(encoding="utf-8")
    )
    assert lineage["inherited_facts"][0]["origin_fact_id"] == origin_id


def test_evolve_root_recorded_and_propagated_to_grandchildren(tmp_path):
    parent, source_result = _make_parent(tmp_path, name="root")
    root_entry = {"path": str(parent), "node_id": "root"}

    gen1 = _seed_child(
        parent, source_result, target=tmp_path / "gen1", evolve_root=root_entry
    )
    assert read_evolve_root(gen1.child_root) == root_entry

    gen1_fact_id = gen1.inherited[0]["child_fact_id"]
    child_source = _verify_inherited_and_add_result(gen1.child_root, gen1_fact_id)
    # Seed the grandchild WITHOUT passing evolve_root: it must propagate.
    gen2 = _seed_child(gen1.child_root, child_source, target=tmp_path / "gen2")
    assert read_evolve_root(gen2.child_root) == root_entry


def test_plain_seed_has_no_evolve_root(tmp_path):
    parent, source_result = _make_parent(tmp_path)
    gen1 = _seed_child(parent, source_result, target=tmp_path / "gen1")
    assert read_evolve_root(gen1.child_root) is None
    lineage = json.loads(
        (gen1.child_root / ".iteris" / "generalize.json").read_text(encoding="utf-8")
    )
    assert "evolve_root" not in lineage

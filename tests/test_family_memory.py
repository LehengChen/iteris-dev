"""Family memory: ledger merge, root resolution, and default-on tagged search."""

from __future__ import annotations

import json

from typer.testing import CliRunner

from iteris.cli import app
from iteris.memory.family import (
    family_search_rows,
    is_family_root,
    load_family_index,
    record_failed_path,
    resolve_family_root,
    update_inputs,
    upsert_family_entries,
)
from iteris.memory.search import search_memory
from iteris.project import init_project, write_json

runner = CliRunner()

ORIGIN = "fact:root:fekete-envelope-bridge:20260101T000000Z"


def _entry(project: str, status: str) -> dict:
    return {
        "origin_fact_id": ORIGIN,
        "claim_summary": "Fekete envelope controls pivot decay.",
        "curated_summary": "Under a Fekete determinant envelope, GECP pivots decay geometrically.",
        "family_relevance": "Load-bearing for any kernel class with envelope FDE.",
        "sightings": [
            {"project": project, "fact_id": f"fact:{project}:x", "status": status,
             "assumptions_scope": f"{project} setting"}
        ],
    }


def _make_root(tmp_path):
    root = tmp_path / "root"
    init_project(root)
    upsert_family_entries(root, [_entry("root", "verified")])
    return root


def test_upsert_merges_sightings_by_origin(tmp_path):
    root = _make_root(tmp_path)
    upsert_family_entries(root, [_entry("gen1", "verified")])
    upsert_family_entries(root, [_entry("gen1-bosonic", "rejected")])
    # Re-curation of an existing sighting replaces, not duplicates.
    upsert_family_entries(root, [_entry("gen1", "verified")])

    index = load_family_index(root)
    assert len(index) == 1
    sightings = index[0]["sightings"]
    assert [(s["project"], s["status"]) for s in sightings] == [
        ("gen1", "verified"),
        ("gen1-bosonic", "rejected"),
        ("root", "verified"),
    ]
    # Junk origins are skipped.
    assert upsert_family_entries(root, [{"origin_fact_id": "bogus"}]) == 0


def test_root_resolution_closure_marker(tmp_path):
    family = tmp_path / "fam"
    family.mkdir()
    (family / ".iteris").mkdir()
    write_json(family / ".iteris" / "FAMILY.json", {"goal": "x", "siblings": []})
    (family / "memory" / "family").mkdir(parents=True)
    child = tmp_path / "child"
    init_project(child)
    write_json(
        child / ".iteris" / "family.json",
        {"family_root": str(family), "sibling_id": "A"},
    )
    assert resolve_family_root(child) == family.resolve()


def test_root_resolution_for_root_descendant_and_outsider(tmp_path):
    root = _make_root(tmp_path)
    assert is_family_root(root)
    assert resolve_family_root(root) == root

    child = tmp_path / "child"
    init_project(child)
    assert resolve_family_root(child) is None  # no lineage, no family dir
    write_json(
        child / ".iteris" / "generalize.json",
        {"schema_version": "iteris.generalize_lineage.v0",
         "evolve_root": {"path": str(root), "node_id": "root"}},
    )
    assert resolve_family_root(child) == root


def test_family_rows_tagged_and_searched_by_default(tmp_path):
    root = _make_root(tmp_path)
    record_failed_path(
        root,
        source_project="gen1-bosonic",
        record={"route": "naive pole bound", "reason": "diverges at omega=0"},
    )
    update_inputs(root, [{"key": "FDE", "statement": "Fekete determinant envelope."}],
                  source_project="gen1")

    child = tmp_path / "child"
    init_project(child)
    write_json(
        child / ".iteris" / "generalize.json",
        {"evolve_root": {"path": str(root), "node_id": "root"}},
    )

    rows = family_search_rows(child)
    assert {row["scope"] for row in rows} == {"family"}

    hits = search_memory(child, "fekete envelope pivot decay")
    family_hits = [h for h in hits if h.get("scope") == "family"]
    assert family_hits and "re-verify locally" in family_hits[0]["hint"]

    dead = search_memory(child, "pole bound diverges omega")
    assert any("dead end" in h.get("hint", "") for h in dead if h.get("scope") == "family")

    # Opt-out works.
    assert all(h.get("scope") != "family" for h in
               search_memory(child, "fekete envelope pivot decay", include_family=False))

    inputs_hit = search_memory(child, "FDE determinant envelope")
    assert any(h.get("key") == "FDE" and "gen1" in h.get("projects", []) for h in inputs_hit)


def test_cli_local_only_flag(tmp_path):
    root = _make_root(tmp_path)
    result = runner.invoke(
        app, ["tool", "memory", "search", str(root), "--query", "fekete envelope", "--json"]
    )
    assert result.exit_code == 0, result.output
    assert any(h.get("scope") == "family" for h in json.loads(result.output))

    result = runner.invoke(
        app,
        ["tool", "memory", "search", str(root), "--query", "fekete envelope", "--local-only", "--json"],
    )
    assert result.exit_code == 0, result.output
    assert all(h.get("scope") != "family" for h in json.loads(result.output))

"""Dashboard evolve data contracts: node outcome, family ledger, direction enrichment."""

from __future__ import annotations

import json

from typer.testing import CliRunner

from iteris.cli import app
from iteris.project import init_project, write_json

runner = CliRunner()


def _invoke(args: list[str]) -> dict:
    result = runner.invoke(app, args)
    assert result.exit_code == 0, result.output
    return json.loads(result.output)


def _family_fixture(tmp_path):
    """Family root + one finished child node with analysis, answer and ledger."""
    root = tmp_path / "root"
    root.mkdir()
    init_project(root)
    child = tmp_path / "child"
    child.mkdir()
    init_project(child)

    (root / "generalize").mkdir(exist_ok=True)
    (root / "generalize" / "auto-01.md").write_text(
        "# Dir One\n\n## Risks\n\nPre-run risk.\n", encoding="utf-8"
    )
    write_json(
        root / "generalize" / "EVOLVE.json",
        {
            "goal": "Generalize the result.",
            "nodes": [
                {
                    "project": "../child",
                    "node_id": "child-node",
                    "kind": "abstract",
                    "seeded_from_direction": "dir-root-01",
                    "analyzed": False,
                    "phase": "goal_success_verified",
                }
            ],
            "direction_pool": [
                {
                    "direction_id": "dir-root-01",
                    "source_node": "root",
                    "title": "Dir One",
                    "status": "verified",
                    "markdown_file": "generalize/auto-01.md",
                },
                {
                    "direction_id": "dir-synthesis-x",
                    "source_node": "synthesis",
                    "title": "Synth",
                    "status": "proposed",
                    "markdown_file": None,
                    "synthesis": True,
                    "target_statement": "Prove X under Y.",
                    "first_steps": ["Import facts.", "Prove X."],
                },
            ],
            "boundary": [],
        },
    )

    (child / "STATUS.md").write_text(
        "phase: goal_success_verified\ntarget_artifact: results/prob/answer_verified.md\n",
        encoding="utf-8",
    )
    answer = child / "results" / "prob" / "answer_verified.md"
    answer.parent.mkdir(parents=True)
    answer.write_text("# Answer\n\nThe theorem holds.\n", encoding="utf-8")
    (child / "generalize").mkdir(exist_ok=True)
    write_json(
        child / "generalize" / "analysis.json",
        {"result_summary": "Proved the child theorem under assumptions A and B.", "directions": []},
    )

    fam = root / "memory" / "family"
    fam.mkdir(parents=True, exist_ok=True)
    rows = [
        {
            "claim_summary": "old claim",
            "curated_summary": "Old curated claim.",
            "family_relevance": "Reusable.",
            "origin_fact_id": "fact:other-node:old:20260101T000000Z",
            "sightings": [{"project": "other-node", "status": "verified"}],
            "updated_at": "2026-01-01T00:00:00Z",
        },
        {
            "claim_summary": "child claim",
            "curated_summary": "Child curated claim with assumptions.",
            "family_relevance": "Reusable for siblings.",
            "origin_fact_id": "fact:child-node:lemma:20260102T000000Z",
            "sightings": [{"project": "child-node", "status": "verified"}],
            "updated_at": "2026-01-02T00:00:00Z",
        },
    ]
    (fam / "FAMILY_INDEX.jsonl").write_text(
        "\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8"
    )
    (fam / "failed_paths.jsonl").write_text(
        json.dumps(
            {
                "ts": "2026-01-03T00:00:00Z",
                "source_project": "child-node",
                "record": {"route": "Bad route.", "reason": "Counterexample."},
            }
        )
        + "\n",
        encoding="utf-8",
    )
    return root


def test_node_outcome_with_analysis_fallback(tmp_path):
    root = _family_fixture(tmp_path)
    payload = _invoke(["tool", "ui", "node", str(root), "--node-id", "child-node", "--json"])
    assert payload["schema_version"] == "iteris.ui_evolve_node.v0"
    assert payload["node"]["node_id"] == "child-node"
    # Node entry has no result_summary — resolved from the child's analysis.json.
    assert payload["result_summary"].startswith("Proved the child theorem")
    assert payload["answer"]["path"] == "results/prob/answer_verified.md"
    assert "The theorem holds." in payload["answer"]["content"]
    # Only this node's curated claims, not the whole ledger.
    assert [c["claim_summary"] for c in payload["family_claims"]] == ["child claim"]


def test_node_unknown_id(tmp_path):
    root = _family_fixture(tmp_path)
    payload = _invoke(["tool", "ui", "node", str(root), "--node-id", "nope", "--json"])
    assert payload["node"] is None


def test_family_ledger_newest_first(tmp_path):
    root = _family_fixture(tmp_path)
    payload = _invoke(["tool", "ui", "family", str(root), "--json"])
    assert payload["schema_version"] == "iteris.ui_family.v0"
    assert [c["claim_summary"] for c in payload["claims"]] == ["child claim", "old claim"]
    assert payload["claims"][0]["origin_node"] == "child-node"
    assert payload["failed_paths"][0]["route"] == "Bad route."
    assert payload["failed_paths"][0]["reason"] == "Counterexample."


def test_direction_seeded_node_carries_result_summary(tmp_path):
    root = _family_fixture(tmp_path)
    payload = _invoke(["tool", "ui", "direction", str(root), "--direction-id", "dir-root-01", "--json"])
    assert payload["seeded_node"]["result_summary"].startswith("Proved the child theorem")
    # Intent markdown still ships for the drawer's proposal-time section.
    assert "Pre-run risk." in payload["content"]


def test_synthesis_direction_inline_intent(tmp_path):
    root = _family_fixture(tmp_path)
    payload = _invoke(["tool", "ui", "direction", str(root), "--direction-id", "dir-synthesis-x", "--json"])
    assert "content" not in payload
    assert payload["direction"]["target_statement"] == "Prove X under Y."
    assert payload["direction"]["first_steps"] == ["Import facts.", "Prove X."]

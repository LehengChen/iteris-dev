from __future__ import annotations

import json

from typer.testing import CliRunner

from iteris.cli import app
from iteris.generalize_analyze import (
    ANALYZE_SCHEMA,
    build_analyze_prompt,
    validate_analysis,
)
from iteris.project import init_project


def _valid_payload():
    return {
        "schema_version": ANALYZE_SCHEMA,
        "parent_project": {"path": "/p", "id": "p", "name": "p"},
        "source_result": "results/p/answer_verified.md",
        "result_summary": "A verified result.",
        "load_bearing_inputs": [{"key": "STP", "statement": "ordered samples are STP", "used_for": "no-growth"}],
        "incidental_machinery": ["specific Bessel estimates"],
        "directions": [
            {
                "id": "01",
                "title": "Abstract to TP kernels",
                "axis": "A_theory",
                "kind": "abstract",
                "one_line": "abstract upward",
                "target_statement": "meta-theorem over TP kernels",
                "uses_inputs": ["STP"],
                "first_steps": ["re-derive from abstract hypotheses"],
                "risks": ["envelope shape must be re-checked"],
                "depends_on": [],
                "scores": {"impact": "H", "tractability": "H", "reuse": "H", "risk": "L"},
                "tier": 1,
                "markdown_file": "generalize/auto-01-abstract.md",
            }
        ],
        "recommended_order": ["01"],
    }


def test_validate_accepts_well_formed_payload():
    result = validate_analysis(_valid_payload())
    assert result["ok"] is True, result["errors"]
    assert result["direction_count"] == 1


def test_validate_rejects_instantiate_without_regularization_target():
    payload = _valid_payload()
    payload["directions"][0]["kind"] = "instantiate"  # now regularization_target is required
    result = validate_analysis(payload)
    assert result["ok"] is False
    assert any("regularization_target" in e for e in result["errors"])


def test_validate_accepts_instantiate_with_regularization_target():
    payload = _valid_payload()
    payload["directions"][0]["kind"] = "instantiate"
    payload["directions"][0]["regularization_target"] = "subtract the omega=0 pole"
    result = validate_analysis(payload)
    assert result["ok"] is True, result["errors"]


def test_validate_rejects_missing_load_bearing_inputs():
    payload = _valid_payload()
    payload["load_bearing_inputs"] = []
    result = validate_analysis(payload)
    assert result["ok"] is False
    assert any("load_bearing_inputs" in e for e in result["errors"])


def test_validate_rejects_bad_kind_and_duplicate_ids():
    payload = _valid_payload()
    payload["directions"].append({**payload["directions"][0], "kind": "weird"})
    result = validate_analysis(payload)
    assert result["ok"] is False
    assert any("kind must be one of" in e for e in result["errors"])
    assert any("duplicate id" in e for e in result["errors"])


def test_validate_rejects_undeclared_uses_inputs():
    payload = _valid_payload()
    payload["directions"][0]["uses_inputs"] = ["STP", "NOPE"]
    result = validate_analysis(payload)
    assert result["ok"] is False
    assert any("undeclared load_bearing key" in e for e in result["errors"])


def test_validate_rejects_unknown_depends_on():
    payload = _valid_payload()
    payload["directions"][0]["depends_on"] = ["99"]
    result = validate_analysis(payload)
    assert result["ok"] is False
    assert any("depends_on references unknown" in e for e in result["errors"])


def test_validate_rejects_recommended_order_not_permutation():
    payload = _valid_payload()
    payload["recommended_order"] = ["01", "02"]  # 02 does not exist
    result = validate_analysis(payload)
    assert result["ok"] is False
    assert any("permutation of the direction ids" in e for e in result["errors"])


def test_validate_accepts_contract_fields():
    payload = _valid_payload()
    payload["directions"][0].update(
        {
            "success_criteria": ["prove the bound for every d >= 2"],
            "does_not_count": ["the product-kernel tensorization warm-up"],
            "audit_routes": ["R1: perturbative product", {"route": "R2: fiber transfer"}],
            "experiment_gate": "run the pivot-growth scan first",
            "novelty_claim": "uses the Zolotarev correspondence, not in the pool",
        }
    )
    result = validate_analysis(payload)
    assert result["ok"] is True, result["errors"]


def test_validate_rejects_malformed_contract_fields():
    payload = _valid_payload()
    payload["directions"][0].update(
        {
            "success_criteria": [],  # empty list is not a contract
            "does_not_count": ["ok", ""],  # blank item
            "audit_routes": [{"hint": "no route key"}],
            "experiment_gate": "   ",
            "novelty_claim": 7,
        }
    )
    result = validate_analysis(payload)
    assert result["ok"] is False
    for needle in (
        "success_criteria",
        "does_not_count",
        "audit_routes[0]",
        "experiment_gate",
        "novelty_claim",
    ):
        assert any(needle in e for e in result["errors"]), (needle, result["errors"])


def test_analyze_prompt_has_required_sections():
    prompt = build_analyze_prompt(
        parent_name="Iteris-GECP",
        source_result_rel="results/prob-test/no_loglog_natural_proof.md",
        n_directions=6,
        analysis_json_path="generalize/analysis.json",
        directions_dir="generalize",
        validate_command="iteris tool generalize analyze . --validate generalize/analysis.json --json",
    )
    # Core method steps and the baked-in lessons must be present.
    assert "load_bearing_inputs" in prompt
    assert "incidental_machinery" in prompt
    assert "regularization_target" in prompt
    assert "not applicable" in prompt  # the anti-premature-negative instruction
    assert "results/prob-test/no_loglog_natural_proof.md" in prompt
    assert ANALYZE_SCHEMA in prompt
    assert "iteris generalize --direction" in prompt
    # Success-contract discipline is part of the method.
    assert "success_criteria" in prompt
    assert "does_not_count" in prompt
    assert "audit_routes" in prompt
    assert "## Success criteria" in prompt  # the md sections the verifier reads


def test_analyze_prompt_family_section_requires_novelty_claim():
    prompt = build_analyze_prompt(
        parent_name="p",
        source_result_rel="results/p/answer.md",
        n_directions=3,
        analysis_json_path="generalize/analysis.json",
        directions_dir="generalize",
        validate_command="validate",
        family_digest="- [vetoed] Certificate Auditor (kind=abstract, uses_inputs=[])",
    )
    assert "novelty_claim" in prompt
    assert "OFF-LIMITS" in prompt


def test_analyze_print_writes_prompt_without_launching(tmp_path):
    project = tmp_path / "proj"
    init_project(project)
    # Give it a verified result to point at.
    target = project / "results" / "proj" / "answer_verified.md"
    target.parent.mkdir(parents=True)
    target.write_text("# result\n", encoding="utf-8")
    (project / "STATUS.md").write_text(f"target_artifact: results/proj/answer_verified.md\n", encoding="utf-8")

    result = CliRunner().invoke(
        app,
        ["tool", "generalize", "analyze", str(project), "--print", "--json"],
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["mode"] == "print"
    assert payload["analysis_json"] == "generalize/analysis.json"
    assert (project / ".iteris" / "generalize_analyze_prompt.txt").exists()
    # Did not create a real session / output.
    assert not (project / "generalize" / "analysis.json").exists()


def test_analyze_validate_mode_reports_errors(tmp_path):
    project = tmp_path / "proj"
    init_project(project)
    bad = project / "generalize" / "analysis.json"
    bad.parent.mkdir(parents=True)
    bad.write_text(json.dumps({"schema_version": "wrong"}), encoding="utf-8")

    result = CliRunner().invoke(
        app,
        ["tool", "generalize", "analyze", str(project), "--validate", "generalize/analysis.json", "--json"],
    )
    assert result.exit_code == 1
    payload = json.loads(result.output)
    assert payload["ok"] is False
    assert payload["errors"]


def test_analyze_validate_mode_accepts_good_file(tmp_path):
    project = tmp_path / "proj"
    init_project(project)
    gen = project / "generalize"
    gen.mkdir(parents=True)
    payload = _valid_payload()
    (gen / "analysis.json").write_text(json.dumps(payload), encoding="utf-8")
    (gen / "auto-01-abstract.md").write_text("# Abstract\n", encoding="utf-8")

    result = CliRunner().invoke(
        app,
        ["tool", "generalize", "analyze", str(project), "--validate", "generalize/analysis.json", "--json"],
    )
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["ok"] is True


def test_family_pool_digest_includes_goal_and_rejection_reasons(tmp_path):
    from iteris.commands.generalize_tool import _family_pool_digest
    from iteris.evolve import init_state, read_state, write_state

    root = tmp_path / "root"
    init_project(root)
    (root / "memory" / "family").mkdir(parents=True)
    init_state(root, goal="push to the most general kernel class; supersede near-duplicates aggressively")
    state = read_state(root)
    state["direction_pool"] = [
        {"direction_id": "dir-a", "title": "Survivor", "status": "approved",
         "kind": "abstract", "uses_inputs": ["STP"]},
        {"direction_id": "dir-b", "title": "Certificate Auditor", "status": "vetoed",
         "kind": "abstract", "uses_inputs": [], "vetoed_why": "third repeat of the genre"},
        {"direction_id": "dir-c", "title": "Old route", "status": "superseded",
         "kind": "abstract", "uses_inputs": [], "superseded_why": "overlaps dir-a"},
    ]
    state["boundary"] = [
        {"direction_id": "dir-x", "verdict": "impossible", "reason_summary": "loses sign-regularity"}
    ]
    write_state(root, state)

    digest = _family_pool_digest(root)
    assert digest is not None
    assert "Family goal" in digest and "supersede near-duplicates" in digest
    assert "VETOED by the human" in digest and "third repeat of the genre" in digest
    assert "superseded: overlaps dir-a" in digest
    assert "[boundary] dir-x: impossible" in digest

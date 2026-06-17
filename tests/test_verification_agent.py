from __future__ import annotations

from pathlib import Path

from iteris.verification.agent import build_agent_prompt, build_codex_command, normalize_agent_output


def test_agent_prompt_and_command_are_codex_exec(tmp_path):
    request_path = tmp_path / "request.json"
    output_path = tmp_path / "verification.json"
    prompt = build_agent_prompt(request_id="verify-test", request_path=request_path, output_path=output_path)

    assert "Iteris Verification Agent" in prompt
    assert str(request_path) in prompt
    assert str(output_path) in prompt
    assert '"verdict": "correct"' in prompt
    assert "well-formed" in prompt
    assert "`goal_success`" in prompt

    cmd = build_codex_command(
        project_root=Path("/tmp/project"),
        prompt=prompt,
        executable="codex",
        model="gpt-5.5",
        reasoning_effort="xhigh",
    )
    assert cmd[:5] == ["codex", "exec", "--json", "-C", "/tmp/project"]
    assert "--dangerously-bypass-approvals-and-sandbox" in cmd
    assert cmd[-1] == "-"
    assert prompt not in cmd


def test_agent_output_normalizes_to_iteris_result(tmp_path):
    project = tmp_path / "project"
    run_dir = project / "verification" / "agent_runs" / "verify-test"
    run_dir.mkdir(parents=True)
    log_path = run_dir / "codex.log"
    log_path.write_text("log", encoding="utf-8")
    request = {
        "request_id": "verify-test",
        "mode": "assembly",
        "claim": "Verify final answer.",
        "artifacts": ["results/problem-001/answer_verified.md"],
        "target_artifact": "results/problem-001/answer_verified.md",
    }
    payload = {
        "verification_report": {"summary": "No gaps.", "critical_errors": [], "gaps": []},
        "verdict": "correct",
        "repair_hints": "",
        "checked_artifacts": ["results/problem-001/answer_verified.md"],
        "checked_fact_ids": ["fact:project:main"],
    }

    result = normalize_agent_output(request=request, payload=payload, run_dir=run_dir, log_path=log_path)

    assert result["backend"] == "agent"
    assert result["verifier"] == "iteris.codex_verification_agent"
    assert result["passed"] is True
    assert result["strict_verdict"] == "correct"
    assert result["claim_ceiling_after_verification"] == "verified"


def test_goal_success_agent_result_has_verified_claim_ceiling(tmp_path):
    project = tmp_path / "project"
    run_dir = project / "verification" / "agent_runs" / "verify-goal"
    run_dir.mkdir(parents=True)
    log_path = run_dir / "codex.log"
    log_path.write_text("log", encoding="utf-8")
    request = {
        "request_id": "verify-goal",
        "mode": "goal_success",
        "claim": "Solve the original problem.",
        "artifacts": ["results/problem-001/answer_verified.md"],
        "target_artifact": "results/problem-001/answer_verified.md",
    }
    payload = {
        "verification_report": {"summary": "The artifact solves the stated goal.", "critical_errors": [], "gaps": []},
        "verdict": "correct",
        "checked_artifacts": ["results/problem-001/answer_verified.md"],
        "checked_fact_ids": [],
    }

    result = normalize_agent_output(request=request, payload=payload, run_dir=run_dir, log_path=log_path)

    assert result["passed"] is True
    assert result["mode"] == "goal_success"
    assert result["claim_ceiling_after_verification"] == "verified"


def test_agent_prompt_contains_contract_and_rigor_clauses(tmp_path):
    from iteris.verification.agent import build_agent_prompt

    prompt = build_agent_prompt(
        request_id="req-1",
        request_path=tmp_path / "request.json",
        output_path=tmp_path / "out.json",
    )
    # Success-contract enforcement for goal_success.
    assert "## Success criteria" in prompt
    assert "What does" in prompt and "NOT count" in prompt
    assert "Audit routes" in prompt
    # External results must be located to a checkable source.
    assert "unnamed \"standard theorem\"" in prompt
    # Experiment mode must check statistical discriminating power.
    assert "near-collinear" in prompt
    # goal_success must reject degenerate instantiations of a universal goal
    # via single-instance/sub-family coverage and objective-collapse checks.
    assert "Scope adequacy and quantifier check" in prompt
    assert "universally quantified" in prompt
    assert "degenerate instantiations" in prompt
    assert "easiest" in prompt and "admissible instance" in prompt
    # assembly must flag overclaiming a universal goal from a sub-case.
    assert "overclaims and is `wrong`" in prompt
    # goal_success verifies coverage against the source, not a self-narrowed claim,
    # demands a matching lower bound for optimal/sharp goals, and refutes by default.
    assert "ORIGINAL SOURCE goal" in prompt
    assert "narrowing the claim" in prompt
    assert "matching lower bound" in prompt
    assert "refuting stance" in prompt


def test_principled_stop_mode_is_registered_and_adversarial():
    # principled_stop is a valid mode with an adversarial verifier rubric
    # and a distinct (non-"verified") claim ceiling.
    from iteris.verification.local import ALLOWED_MODES
    from iteris.verification.agent import _claim_ceiling

    assert "principled_stop" in ALLOWED_MODES
    assert _claim_ceiling("principled_stop", True) == "reduced"
    assert _claim_ceiling("principled_stop", False) == "submitted"
    assert _claim_ceiling("goal_success", True) == "verified"

    prompt = build_agent_prompt(
        request_id="r", request_path=Path("req.json"), output_path=Path("out.json")
    )
    assert "`principled_stop`" in prompt
    assert "verified impossibility result" in prompt  # requires a real obstruction
    assert "reduction to a named" in prompt  # ... or a precise reduction
    assert "default to" in prompt  # defaults to reject
    assert "not a license to give up" in prompt.lower()
    # ... and an honest strong-partial on a research-open goal is a valid landing.
    assert "research-OPEN goal" in prompt

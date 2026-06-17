"""Tests for the framework optimization pass: boundary inheritance, reference
intake, panel verification, event-schema unification, and fact hygiene."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from iteris.bootstrap import run_once
from iteris.cli import app
from iteris.commands.context import build_context, keystone_verification_counts
from iteris.commands.goal import build_project_context_lines
from iteris.frontier import load_frontier_index, save_frontier_index
from iteris.inherit import inherit_boundary, select_boundary_rows
from iteris.memory.facts import fact_in_degrees, keystone_facts, rebuild_fact_index, validate_fact_file, write_fact
from iteris.memory.scratch import append as scratch_append
from iteris.memory.scratch import read_events
from iteris.project import append_jsonl, init_project, read_json, write_json
from iteris.references import import_references
from iteris.tasks import load_task_pool
from iteris.verification.panel import verify_panel

SOURCE = r"""
\begin{problem}
Prove a sharper stability estimate for a bounded linear operator.
\end{problem}
"""


def _make_project(tmp_path: Path, name: str) -> Path:
    source = tmp_path / f"{name}-problem.tex"
    source.write_text(SOURCE, encoding="utf-8")
    project = tmp_path / name
    init_project(project, source=source)
    return project


def _add_fact(project: Path, local_id: str, *, status: str, summary: str, fact_type: str = "claim") -> str:
    fact_id = f"fact:{project.name}:{local_id}"
    write_fact(
        project,
        fact_id=fact_id,
        source_task="task-test",
        claim_summary=summary,
        statement=f"Statement body for {local_id}.",
        status=status,
        fact_type=fact_type,
        verification="verify-test" if status == "verified" else None,
        review_level="verified" if status == "verified" else "none",
    )
    return fact_id


# ---------------------------------------------------------------------------
# Boundary inheritance


def _parent_with_boundary(tmp_path: Path) -> Path:
    parent = _make_project(tmp_path, "parent")
    _add_fact(parent, "blocker-one", status="verified", summary="Route A is blocked: coherence insufficient for the product step.")
    _add_fact(parent, "rejected-one", status="rejected", summary="Naive barrier lemma holds for all spectra.")
    _add_fact(parent, "positive-one", status="verified", summary="Reduction theorem relating the two formulations.")
    _add_fact(parent, "draft-one", status="draft", summary="Half-formed idea about obstruction patterns blocked somewhere.")
    rebuild_fact_index(parent)
    frontier = load_frontier_index(parent)
    frontier["do_not_schedule_patterns"] = [{"pattern": "KL-descent relative-error route", "reason": "refuted twice"}]
    frontier["closed_lanes"] = [{"lane_id": "lane-quotient-kl", "title": "Quotient KL normal coordinates", "reason": "verified wall"}]
    save_frontier_index(parent, frontier)
    return parent


def test_select_boundary_rows_picks_blockers_and_rejections(tmp_path):
    parent = _parent_with_boundary(tmp_path)
    rows = select_boundary_rows(parent)
    kinds = {row["fact_id"].split(":")[-1]: row["boundary_kind"] for row in rows}
    assert kinds == {"blocker-one": "verified_blocker", "rejected-one": "rejected_claim"}


def test_inherit_boundary_imports_advisory_facts_and_patterns(tmp_path):
    parent = _parent_with_boundary(tmp_path)
    child = _make_project(tmp_path, "child")

    result = inherit_boundary(child, parent)

    assert sorted(result["boundary_kinds"].items()) == [("rejected_claim", 1), ("verified_blocker", 1)]
    index_rows = [json.loads(line) for line in (child / "memory/facts/FACT_INDEX.jsonl").read_text().splitlines()]
    inherited = [row for row in index_rows if row.get("fact_type") == "inherited_boundary"]
    assert len(inherited) == 2
    assert all(row["status"] == "reviewed" for row in inherited)
    assert all(row["claim_policy"] == "inherited_boundary_advisory" for row in inherited)
    # origin ids point back at the parent facts
    origins = {row["origin_fact_id"] for row in inherited}
    assert f"fact:{parent.name}:blocker-one" in origins

    frontier = load_frontier_index(child)
    patterns = frontier["do_not_schedule_patterns"]
    assert {item.get("inherited_source_field") for item in patterns} == {"do_not_schedule_patterns", "closed_lanes"}
    assert all(item["inherited_from"] == parent.name for item in patterns)

    summary_path = child / result["summary_path"]
    assert summary_path.exists()
    assert "rejected_claim" in summary_path.read_text()

    lineage = read_json(child / ".iteris" / "inherit.json")
    assert lineage["parents"][0]["parent_project"] == parent.name

    events = [event for event in read_events(child) if event["event_type"] == "boundary_inherited"]
    assert events and events[0]["imported_fact_count"] == 2


def test_inherit_boundary_is_idempotent(tmp_path):
    parent = _parent_with_boundary(tmp_path)
    child = _make_project(tmp_path, "child")
    first = inherit_boundary(child, parent)
    second = inherit_boundary(child, parent)
    assert sorted(first["imported_facts"]) == sorted(second["imported_facts"])
    index_rows = [json.loads(line) for line in (child / "memory/facts/FACT_INDEX.jsonl").read_text().splitlines()]
    inherited = [row for row in index_rows if row.get("fact_type") == "inherited_boundary"]
    assert len(inherited) == 2
    frontier = load_frontier_index(child)
    assert len(frontier["do_not_schedule_patterns"]) == 2  # replaced, not duplicated
    lineage = read_json(child / ".iteris" / "inherit.json")
    assert len(lineage["parents"]) == 1


def test_inherit_boundary_rejects_self(tmp_path):
    parent = _parent_with_boundary(tmp_path)
    with pytest.raises(ValueError):
        inherit_boundary(parent, parent)


def test_frontier_inherit_cli(tmp_path):
    parent = _parent_with_boundary(tmp_path)
    child = _make_project(tmp_path, "child")
    result = CliRunner().invoke(
        app,
        ["tool", "frontier", "inherit", str(child), "--from", str(parent), "--json"],
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert len(payload["imported_facts"]) == 2


def test_new_with_references_and_inherit_frontier(tmp_path):
    parent = _parent_with_boundary(tmp_path)
    refs = tmp_path / "paper-pack"
    refs.mkdir()
    (refs / "paper1.txt").write_text("reference text", encoding="utf-8")
    source = tmp_path / "problem.tex"
    source.write_text(SOURCE, encoding="utf-8")
    project = tmp_path / "fresh"

    result = CliRunner().invoke(
        app,
        [
            "new",
            str(project),
            "--source",
            str(source),
            "--references",
            str(refs),
            "--inherit-frontier",
            str(parent),
            "--json",
        ],
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["references"]["total_files"] == 1
    assert len(payload["inherited_boundary"]["imported_facts"]) == 2
    assert (project / "references" / "user" / "paper-pack" / "paper1.txt").exists()
    assert (project / "references" / "MANIFEST.json").exists()

    lines = "".join(build_project_context_lines(project))
    assert "references/MANIFEST.json" in lines
    assert "inherited" in lines and parent.name in lines


# ---------------------------------------------------------------------------
# References manifest


def test_import_references_merges_manifest(tmp_path):
    project = _make_project(tmp_path, "refs")
    one = tmp_path / "one.pdf"
    one.write_bytes(b"%PDF-1.4 fake")
    import_references(project, [one])
    two = tmp_path / "two.md"
    two.write_text("notes", encoding="utf-8")
    result = import_references(project, [two])
    manifest = read_json(project / "references" / "MANIFEST.json")
    assert result["manifest_entries"] == 2
    assert {entry["path"] for entry in manifest["entries"]} == {
        "references/user/one.pdf",
        "references/user/two.md",
    }


def test_import_references_missing_path_raises(tmp_path):
    project = _make_project(tmp_path, "refs2")
    with pytest.raises(FileNotFoundError):
        import_references(project, [tmp_path / "missing.pdf"])


# ---------------------------------------------------------------------------
# Panel verification


def _seat_factory(verdicts: list[dict]):
    calls: list[dict] = []

    def runner(project_root, **kwargs):
        spec = verdicts[len(calls)]
        calls.append(kwargs)
        if spec.get("raise"):
            raise RuntimeError("seat crashed")
        return {
            "request_id": f"verify-seat-{len(calls)}",
            "verdict": spec["verdict"],
            "passed": spec["verdict"] == "accepted",
            "summary": "seat summary",
            "critical_errors": spec.get("critical_errors", []),
            "gaps": spec.get("gaps", []),
            "checked_artifacts": ["memory/facts/fact-test.md"],
            "checked_fact_ids": ["fact:test:one"],
        }

    return runner, calls


def test_verify_panel_unanimous_accept(tmp_path):
    project = _make_project(tmp_path, "panel")
    runner, calls = _seat_factory([{"verdict": "accepted"}, {"verdict": "accepted"}])
    result = verify_panel(project, mode="fact", claim="claim", artifacts=[], runs=2, seat_runner=runner)
    assert result["passed"] is True
    assert result["verdict"] == "accepted"
    assert len(calls) == 2
    assert result["checked_fact_ids"] == ["fact:test:one"]
    # persisted like a normal verification result, so promote-fact can find it
    stored = read_json(project / "verification" / "results" / f"{result['request_id']}.json")
    assert stored["passed"] is True
    assert result["request_id"].startswith("verify-panel-")


def test_verify_panel_one_rejection_fails(tmp_path):
    project = _make_project(tmp_path, "panel2")
    runner, _ = _seat_factory(
        [
            {"verdict": "accepted"},
            {"verdict": "rejected", "critical_errors": [{"location": "step 3", "issue": "constant is wrong"}]},
        ]
    )
    result = verify_panel(project, mode="fact", claim="claim", artifacts=[], runs=2, seat_runner=runner)
    assert result["passed"] is False
    assert result["verdict"] == "rejected"
    assert any("constant is wrong" in item["issue"] for item in result["critical_errors"])


def test_verify_panel_seat_crash_counts_as_failure(tmp_path):
    project = _make_project(tmp_path, "panel3")
    runner, _ = _seat_factory([{"verdict": "accepted"}, {"raise": True}])
    result = verify_panel(project, mode="fact", claim="claim", artifacts=[], runs=2, seat_runner=runner)
    assert result["passed"] is False
    assert result["verdict"] == "rejected"
    assert any("seat crashed" in item["issue"] for item in result["critical_errors"])


def test_verify_panel_rejects_bad_inputs(tmp_path):
    project = _make_project(tmp_path, "panel4")
    with pytest.raises(ValueError):
        verify_panel(project, mode="nope", claim="x", artifacts=[], runs=2)
    with pytest.raises(ValueError):
        verify_panel(project, mode="fact", claim="x", artifacts=[], runs=0)


# ---------------------------------------------------------------------------
# Event schema unification


def test_scratch_events_use_envelope_and_read_events_normalizes(tmp_path):
    project = _make_project(tmp_path, "events")
    scratch_append(project, "observations", {"event_type": "obs", "note": "x"})
    # legacy flat record, as written by pre-unification code
    append_jsonl(
        project / "memory" / "scratch" / "events.jsonl",
        {"timestamp": "2026-06-12T00:00:00Z", "event_type": "legacy_flat", "value": 1},
    )
    raw_lines = [
        json.loads(line)
        for line in (project / "memory" / "scratch" / "events.jsonl").read_text().splitlines()
    ]
    fresh = [row for row in raw_lines if row.get("record", {}).get("event_type") == "scratch_append"]
    assert fresh, "cross-posted scratch stub should use the envelope schema"

    events = read_events(project)
    types = [event["event_type"] for event in events]
    assert "scratch_append" in types
    assert "legacy_flat" in types
    legacy = next(event for event in events if event["event_type"] == "legacy_flat")
    assert legacy["value"] == 1


def test_fact_add_then_resubmit_emits_fact_update(tmp_path):
    project = _make_project(tmp_path, "factev")
    args = [
        "tool",
        "memory",
        "add-fact",
        str(project),
        "--source-task",
        "task-x",
        "--claim-summary",
        "stable claim",
        "--statement",
        "body",
        "--fact-id",
        "fact:factev:one",
        "--json",
    ]
    first = CliRunner().invoke(app, args)
    assert first.exit_code == 0, first.output
    assert json.loads(first.output)["resubmit"] is False
    second = CliRunner().invoke(app, args)
    assert second.exit_code == 0, second.output
    assert json.loads(second.output)["resubmit"] is True
    types = [event["event_type"] for event in read_events(project)]
    assert types.count("fact_add") == 1
    assert types.count("fact_update") == 1


def test_add_fact_cannot_mint_verified(tmp_path):
    project = _make_project(tmp_path, "factgate")
    result = CliRunner().invoke(
        app,
        [
            "tool",
            "memory",
            "add-fact",
            str(project),
            "--source-task",
            "task-x",
            "--claim-summary",
            "claim",
            "--statement",
            "body",
            "--status",
            "verified",
            "--verification",
            "verify-fake",
        ],
    )
    assert result.exit_code != 0
    assert "promote-fact" in result.output


def test_structural_verification_cannot_pass_inherited_boundary_fact(tmp_path):
    parent = _parent_with_boundary(tmp_path)
    child = _make_project(tmp_path, "childsec")
    result = inherit_boundary(child, parent)
    inherited_id = result["imported_facts"][0]
    from iteris.verification.local import verify_local

    outcome = verify_local(child, mode="fact", claim="re-check inherited fact", artifacts=[], fact_ids=[inherited_id])
    assert outcome["passed"] is False
    assert outcome["verdict"] == "needs_repair"
    assert any("agent or panel re-verification" in gap["issue"] for gap in outcome["gaps"])


def test_rejection_streaks_roll_up_panel_seats(tmp_path):
    from iteris.verification.local import rejection_streaks

    seat = {
        "mode": "fact",
        "claim": "keystone lemma",
        "verdict": "rejected",
        "created_at": "2026-06-12T00:00:01Z",
        "panel_request_id": "verify-panel-x",
    }
    aggregate = {
        "mode": "fact",
        "claim": "keystone lemma",
        "verdict": "rejected",
        "created_at": "2026-06-12T00:00:02Z",
        "request_id": "verify-panel-x",
    }
    # one failed 2-seat panel = one episode, not a 3-long streak
    results = [dict(seat), dict(seat), aggregate]
    assert rejection_streaks(results, min_streak=3) == []
    assert rejection_streaks(results, min_streak=1)[0]["consecutive_rejections"] == 1


def test_promote_fact_demotes_with_failed_verification(tmp_path):
    from iteris.project import write_json as _write_json

    project = _make_project(tmp_path, "demote")
    fact_id = _add_fact(project, "shaky", status="submitted", summary="Shaky lemma")
    rebuild_fact_index(project)
    _write_json(
        project / "verification" / "results" / "verify-failed-1.json",
        {
            "request_id": "verify-failed-1",
            "mode": "fact",
            "claim": "Shaky lemma",
            "verdict": "rejected",
            "passed": False,
            "checked_fact_ids": [fact_id],
        },
    )
    demote = CliRunner().invoke(
        app,
        [
            "tool", "memory", "promote-fact", str(project),
            "--fact-id", fact_id,
            "--verification", "verify-failed-1",
            "--status", "rejected",
            "--review-level", "none",
            "--json",
        ],
    )
    assert demote.exit_code == 0, demote.output
    promote = CliRunner().invoke(
        app,
        [
            "tool", "memory", "promote-fact", str(project),
            "--fact-id", fact_id,
            "--verification", "verify-failed-1",
            "--status", "verified",
        ],
    )
    assert promote.exit_code != 0


def test_frontier_refresh_skips_inherited_boundary_facts(tmp_path):
    from iteris.frontier import frontier_health, refresh_frontier_from_project

    parent = _parent_with_boundary(tmp_path)
    child = _make_project(tmp_path, "childfr")
    inherit_boundary(child, parent)
    refreshed = refresh_frontier_from_project(child)
    inherited_in_routes = [
        fact_id
        for entry in refreshed["active_frontiers"]
        for fact_id in entry.get("fact_ids", [])
        if "inherited-boundary" in fact_id
    ]
    assert inherited_in_routes == []
    health = frontier_health(child)
    assert all(not report.get("explore_recommended") for report in health["frontiers"])


def test_validate_warns_on_verified_fact_without_review_level(tmp_path):
    project = _make_project(tmp_path, "trust")
    fact_id = f"fact:{project.name}:odd"
    path = write_fact(
        project,
        fact_id=fact_id,
        source_task="task-x",
        claim_summary="verified but unreviewed",
        statement="body",
        status="verified",
        verification="verify-x",
        review_level="none",
    )
    report = validate_fact_file(path)
    assert report["ok"] is True
    assert any("review_level" in warning for warning in report["warnings"])


# ---------------------------------------------------------------------------
# Bootstrap umbrella task and keystones


def test_bootstrap_closes_intake_task(tmp_path):
    project = _make_project(tmp_path, "boot")
    result = run_once(project)
    pool = load_task_pool(project)
    task = next(item for item in pool["tasks"] if item["task_id"] == result["task_id"])
    assert task["status"] == "done"
    task_record = read_json(project / "tasks" / f"{result['task_id']}.json")
    assert task_record["status"] == "done"


def test_keystone_facts_flags_under_verified_high_in_degree(tmp_path):
    project = _make_project(tmp_path, "keystone")
    base = _add_fact(project, "base", status="verified", summary="Keystone theorem everything routes through.")
    for index in range(3):
        dependent_id = f"fact:{project.name}:dep-{index}"
        write_fact(
            project,
            fact_id=dependent_id,
            source_task="task-test",
            claim_summary=f"Dependent {index}",
            statement="body",
            status="verified",
            predecessors=[base],
            verification="verify-test",
            review_level="verified",
        )
    rebuild_fact_index(project)
    degrees = fact_in_degrees(project)
    assert degrees[base] == 3
    keystones = keystone_facts(project, verification_counts={base: 1})
    assert [item["fact_id"] for item in keystones] == [base]
    assert keystones[0]["under_verified"] is True
    keystones = keystone_facts(project, verification_counts={base: 2})
    assert keystones[0]["under_verified"] is False

    context = build_context(project)
    attention = context["attention"]
    assert [item["fact_id"] for item in attention["under_verified_keystones"]] == [base]
    assert "verify panel" in attention["guidance"]


def test_keystone_counts_credit_only_targeted_fact():
    """A keystone verified once but cited in many predecessor bundles must count
    as depth 1, not once per incidental bundle mention."""
    base = "fact:k:base"
    verifications = [
        {"mode": "fact", "passed": True, "verification_scope": "codex_agent",
         "primary_fact_ids": [base], "checked_fact_ids": [base]},
        *[
            {"mode": "fact", "passed": True, "verification_scope": "codex_agent",
             "primary_fact_ids": [f"fact:dep-{i}"],
             "checked_fact_ids": [f"fact:dep-{i}", base]}
            for i in range(3)
        ],
    ]
    counts = keystone_verification_counts(verifications)
    assert counts[base] == 1  # NOT 4 — the 3 bundle mentions don't count
    assert counts["fact:dep-0"] == 1


def test_keystone_counts_panel_weight_and_legacy_fallback():
    """Panel aggregates count by seat weight (against their primary only); results
    predating primary_fact_ids fall back to the old checked_fact_ids behavior."""
    base = "fact:k:base"
    verifications = [
        # legacy result (no primary_fact_ids) -> fall back to checked bundle
        {"mode": "fact", "passed": True, "verification_scope": "codex_agent",
         "checked_fact_ids": [base]},
        # panel aggregate: weight 3, against its primary only
        {"mode": "fact", "passed": True, "verification_scope": "agent_panel",
         "panel_runs": 3, "primary_fact_ids": [base],
         "checked_fact_ids": [base, "fact:other"]},
    ]
    counts = keystone_verification_counts(verifications)
    assert counts[base] == 1 + 3
    assert "fact:other" not in counts  # predecessor in the panel bundle not credited


def test_keystone_counts_empty_primary_credits_nothing():
    """An explicit-but-empty primary set (a fact verification submitted with no
    --fact-id) must credit nothing, not fall back to the bundle and re-credit
    predecessors. A legacy result (no primary_fact_ids key) still falls back."""
    base = "fact:k:base"
    present_empty = {
        "mode": "fact", "passed": True, "verification_scope": "codex_agent",
        "primary_fact_ids": [], "checked_fact_ids": [base, "fact:pred"],
    }
    legacy = {
        "mode": "fact", "passed": True, "verification_scope": "codex_agent",
        "checked_fact_ids": [base],
    }
    assert keystone_verification_counts([present_empty]) == {}
    assert keystone_verification_counts([legacy]) == {base: 1}


def test_keystone_counts_ignore_non_fact_failed_and_seat_results():
    base = "fact:k:base"
    verifications = [
        {"mode": "assembly", "passed": True, "verification_scope": "codex_agent",
         "primary_fact_ids": [base]},  # not a fact verification
        {"mode": "fact", "passed": True, "verification_scope": "agent_panel",
         "panel_request_id": "verify-x", "primary_fact_ids": [base]},  # individual seat
        {"mode": "fact", "passed": False, "verification_scope": "codex_agent",
         "primary_fact_ids": [base]},  # failed
    ]
    assert keystone_verification_counts(verifications) == {}


def test_build_context_keystone_not_hidden_by_bundle_mentions(tmp_path):
    """End-to-end: a high in-degree keystone verified once stays flagged even when
    every dependent's verification lists it in checked_fact_ids. Under the old
    bundle-crediting bug it would reach passed=4 and be silently hidden."""
    project = _make_project(tmp_path, "keystone-bundle")
    base = _add_fact(project, "base", status="verified", summary="Keystone.")
    dep_ids = []
    for index in range(3):
        dep = f"fact:{project.name}:dep-{index}"
        write_fact(
            project, fact_id=dep, source_task="task-test",
            claim_summary=f"Dependent {index}", statement="body",
            status="verified", predecessors=[base],
            verification="verify-test", review_level="verified",
        )
        dep_ids.append(dep)
    rebuild_fact_index(project)
    results_dir = project / "verification" / "results"
    results_dir.mkdir(parents=True, exist_ok=True)
    # one real verification OF base, plus three OF deps that each cite base
    bundles = [(base, [base])] + [(d, [d, base]) for d in dep_ids]
    for i, (primary, checked) in enumerate(bundles):
        write_json(results_dir / f"verify-{i:03d}.json", {
            "schema_version": "iteris.verification_result.v0",
            "request_id": f"verify-{i:03d}", "mode": "fact", "passed": True,
            "verdict": "accepted", "verification_scope": "codex_agent",
            "primary_fact_ids": [primary], "checked_fact_ids": checked,
        })
    flagged = [
        item["fact_id"]
        for item in build_context(project)["attention"]["under_verified_keystones"]
    ]
    assert base in flagged


# ---------------------------------------------------------------------------
# Batch 2a


def test_unread_summary_inlines_high_priority_bodies(tmp_path):
    from iteris.messages import ack as message_ack
    from iteris.messages import send as message_send
    from iteris.messages import unread_summary

    project = _make_project(tmp_path, "msgs")
    message_send(project, body="low priority nudge", priority="normal")
    high = message_send(project, body="URGENT: read the boundary digest " + "x" * 800, priority="high", sender="human", type="hint")

    summary = unread_summary(project)
    assert summary["count"] == 2 and summary["high"] == 1
    inlined = summary["high_messages"]
    assert len(inlined) == 1
    assert inlined[0]["msg_id"] == high["msg_id"]
    assert inlined[0]["body"].startswith("URGENT")
    assert inlined[0]["truncated"] is True
    assert "[truncated" in inlined[0]["body"]
    assert "ack" in summary["ack_command"]
    assert summary["guidance"]

    message_ack(project, msg_id=high["msg_id"], disposition="applied")
    summary = unread_summary(project)
    assert summary["count"] == 1 and summary.get("high_messages") is None

    context = build_context(project)
    assert context["unread_messages"]["count"] == 1


def test_status_md_staleness_in_attention(tmp_path):
    import os
    import time as time_module

    project = _make_project(tmp_path, "stale")
    run_once(project)
    old = time_module.time() - 3 * 3600
    os.utime(project / "STATUS.md", (old, old))
    context = build_context(project)
    lag = context["attention"]["status_md_stale_hours"]
    assert lag is not None and 2.5 <= lag <= 3.5
    assert "STATUS.md" in context["attention"]["guidance"]

    (project / "STATUS.md").write_text("phase: fresh\n", encoding="utf-8")
    context = build_context(project)
    assert context["attention"]["status_md_stale_hours"] is None


def test_fact_in_degrees_counts_body_citations_once(tmp_path):
    project = _make_project(tmp_path, "cite")
    base = _add_fact(project, "base", status="verified", summary="Base theorem")
    # cites base in body only (no predecessors metadata)
    write_fact(
        project,
        fact_id=f"fact:{project.name}:body-citer",
        source_task="task-test",
        claim_summary="Uses base via body citation",
        statement=f"By {base}, the bound follows.",
        status="verified",
        verification="verify-test",
        review_level="verified",
    )
    # cites base in BOTH predecessors and body: must count once
    write_fact(
        project,
        fact_id=f"fact:{project.name}:double-citer",
        source_task="task-test",
        claim_summary="Uses base in both channels",
        statement=f"Combine {base} with the lemma.",
        status="verified",
        predecessors=[base],
        verification="verify-test",
        review_level="verified",
    )
    rebuild_fact_index(project)
    degrees = fact_in_degrees(project)
    assert degrees[base] == 2
    assert fact_in_degrees(project, include_body_citations=False)[base] == 1


def test_experiment_verification_requires_baseline(tmp_path):
    from iteris.verification.local import verify_local

    project = _make_project(tmp_path, "exp")
    artifact = project / "artifacts" / "experiments" / "result.md"
    artifact.parent.mkdir(parents=True, exist_ok=True)
    artifact.write_text("# Experiment\n\n58/58 instances passed.\n", encoding="utf-8")
    result = verify_local(project, mode="experiment", claim="descent certified empirically", artifacts=[artifact])
    assert result["verdict"] == "needs_repair"
    assert any("baseline" in gap["issue"] for gap in result["gaps"])

    artifact.write_text(
        "# Experiment\n\n58/58 adversarial instances passed; fixed Ginibre baseline passes 3/58 "
        "(control), so the suite discriminates.\n",
        encoding="utf-8",
    )
    result = verify_local(project, mode="experiment", claim="descent certified empirically", artifacts=[artifact])
    assert result["verdict"] == "accepted"


def test_inherit_truncation_keeps_rejected_first_then_newest(tmp_path):
    import os
    import time as time_module

    parent = _make_project(tmp_path, "bigparent")
    now = time_module.time()
    for index in range(4):
        fid = _add_fact(parent, f"blocker-{index}", status="verified", summary=f"Route {index} is blocked by a wall.")
        path = parent / "memory" / "facts" / f"fact-bigparent-blocker-{index}.md"
        stamp = now - (4 - index) * 600  # blocker-3 newest
        os.utime(path, (stamp, stamp))
    _add_fact(parent, "dead-claim", status="rejected", summary="Refuted approach.")
    rebuild_fact_index(parent)

    child = _make_project(tmp_path, "smallchild")
    result = inherit_boundary(child, parent, max_facts=2)
    assert result["truncated_fact_count"] == 3
    # rejected claim first, then the newest blocker
    assert "dead-claim" in result["imported_facts"][0]
    assert "blocker-3" in result["imported_facts"][1]
    with pytest.raises(ValueError):
        inherit_boundary(child, parent, max_facts=0)

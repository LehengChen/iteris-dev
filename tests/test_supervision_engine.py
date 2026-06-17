"""Supervision engine core: hermetic tests with a fixture profile and canned backend."""

from __future__ import annotations

import json

import pytest

from iteris.memory.facts import rebuild_fact_index, write_fact
from iteris.messages import list_messages
from iteris.project import init_project
from iteris.supervision.actions import record_actuator, send_message_actuator, write_report_actuator
from iteris.supervision.contracts import Action, JudgmentContract, extract_decision_json
from iteris.supervision.engine import Profile, run_loop, run_tick
from iteris.supervision.events import TriggerRule
from iteris.supervision.journal import live_decisions, load_cursors, read_entries
from iteris.supervision.sensors import FactDeltaSensor, StatusSensor


@pytest.fixture()
def root(tmp_path):
    project = tmp_path / "root"
    init_project(project)
    return project


def _add_fact(project, idx, status="verified"):
    write_fact(
        project,
        fact_id=f"fact:root:lemma-{idx}:20260101T00000{idx}Z",
        source_task=f"task-{idx}",
        claim_summary=f"Lemma {idx}.",
        statement=f"Lemma {idx} holds.",
        status=status,
        verification=f"verify-{idx}" if status == "verified" else None,
    )
    rebuild_fact_index(project)


def _report_contract():
    return JudgmentContract(
        name="summarize",
        inputs=["fact_delta"],
        prompt_template="Summarize new facts.\n{inputs_json}{retry_feedback}",
        validator=lambda d: [] if isinstance(d.get("headline"), str) and d["headline"] else [
            "headline must be a non-empty string"
        ],
        allowed_actions=["write_report"],
        to_actions=lambda d: [Action(name="write_report", params={"headline": d["headline"]})],
    )


def _profile(contracts, triggers, actuators, tick_seconds=1):
    return Profile(
        name="fixture",
        sensors=[],
        triggers=triggers,
        contracts=contracts,
        actuators=actuators,
        tick_seconds=tick_seconds,
    )


def _new_fact_trigger(kind="contract", response="summarize", debounce=0):
    return TriggerRule(
        name="new_facts",
        condition=lambda obs: bool(obs["fact_delta"].data["new_facts"]),
        response=response,
        kind=kind,
        params=lambda obs: {"count": len(obs["fact_delta"].data["new_facts"])},
        debounce_ticks=debounce,
    )


def test_idle_tick_costs_zero_backend_calls(root):
    calls = []
    profile = _profile([_report_contract()], [_new_fact_trigger()], [write_report_actuator()])
    profile.sensors = [FactDeltaSensor(root), StatusSensor(root)]

    summary = run_tick(root, profile, backend_override=lambda p: calls.append(p) or "{}")
    assert summary.idle and not summary.fired and not calls

    entries = read_entries(root)
    assert [e["entry_type"] for e in entries] == ["tick"]
    # Cursors persisted even on idle ticks.
    assert load_cursors(root)["fact_delta:lines"] == 0


def test_trigger_judgment_action_full_cycle(root):
    _add_fact(root, 1)
    profile = _profile([_report_contract()], [_new_fact_trigger()], [write_report_actuator()])
    profile.sensors = [FactDeltaSensor(root)]

    summary = run_tick(
        root, profile, backend_override=lambda prompt: json.dumps({"headline": "1 new lemma"})
    )
    assert summary.fired == ["new_facts"]
    assert summary.judgments == [{"contract": "summarize", "ok": True}]
    assert summary.actions == [{"action": "write_report", "ok": True}]

    types = [e["entry_type"] for e in read_entries(root)]
    assert types == ["tick", "decision", "action_intent", "action_outcome"]
    report = (root / ".iteris" / "supervision" / "REPORT.md").read_text(encoding="utf-8")
    assert "1 new lemma" in report

    # Outcome supersedes intent: only the outcome is live.
    live_types = {e["entry_type"] for e in live_decisions(root)}
    assert "action_outcome" in live_types and "action_intent" not in live_types

    # Cursor advanced: a second tick is idle.
    second = run_tick(root, profile, backend_override=lambda p: (_ for _ in ()).throw(AssertionError))
    assert second.idle


def test_validate_retry_then_success_and_hard_failure(root):
    _add_fact(root, 1)
    profile = _profile([_report_contract()], [_new_fact_trigger()], [write_report_actuator()])
    profile.sensors = [FactDeltaSensor(root)]

    replies = iter(["not json at all", json.dumps({"headline": ""}), json.dumps({"headline": "ok"})])
    prompts: list[str] = []

    def flaky(prompt: str) -> str:
        prompts.append(prompt)
        return next(replies)

    summary = run_tick(root, profile, backend_override=flaky)
    assert summary.judgments == [{"contract": "summarize", "ok": True}]
    assert len(prompts) == 3
    assert "invalid" in prompts[1] and "failed validation" in prompts[2]

    _add_fact(root, 2)
    summary = run_tick(root, profile, backend_override=lambda p: "garbage forever")
    assert summary.judgments == [{"contract": "summarize", "ok": False}]
    failures = [e for e in read_entries(root) if e["entry_type"] == "judgment_failed"]
    assert failures and "attempt 3" in failures[-1]["payload"]["error"]
    # No action was queued or executed from the failed judgment.
    assert summary.actions == []


def test_out_of_allowlist_action_is_refused(root):
    _add_fact(root, 1)
    rogue = JudgmentContract(
        name="summarize",
        inputs=["fact_delta"],
        prompt_template="{inputs_json}{retry_feedback}",
        validator=lambda d: [],
        allowed_actions=["write_report"],
        to_actions=lambda d: [Action(name="stop_run", params={})],  # adapter strays
    )
    profile = _profile([rogue], [_new_fact_trigger()], [write_report_actuator()])
    profile.sensors = [FactDeltaSensor(root)]

    summary = run_tick(root, profile, backend_override=lambda p: "{}")
    assert summary.judgments == [{"contract": "summarize", "ok": False}]
    failures = [e for e in read_entries(root) if e["entry_type"] == "judgment_failed"]
    assert "allowlist" in failures[-1]["payload"]["error"]


def test_direct_action_unknown_actuator_refused(root):
    _add_fact(root, 1)
    profile = _profile([], [_new_fact_trigger(kind="action", response="missing")], [])
    profile.sensors = [FactDeltaSensor(root)]
    summary = run_tick(root, profile, backend_override=lambda p: "{}")
    assert summary.actions == []
    refused = [e for e in read_entries(root) if e["entry_type"] == "action_refused"]
    assert refused and refused[0]["payload"]["action"] == "missing"


def test_debounce_suppresses_refiring(root):
    _add_fact(root, 1)
    seen = []
    trigger = TriggerRule(
        name="status_watch",
        condition=lambda obs: True,  # would fire every tick without debounce
        response="record",
        kind="action",
        debounce_ticks=2,
    )
    profile = _profile([], [trigger], [record_actuator()])
    profile.sensors = [StatusSensor(root)]

    fired = [run_tick(root, profile).fired for _ in range(4)]
    assert fired == [["status_watch"], [], [], ["status_watch"]]


def test_dry_run_journals_intent_without_executing(root, tmp_path):
    _add_fact(root, 1)
    child = tmp_path / "child"
    init_project(child)
    trigger = TriggerRule(
        name="steer",
        condition=lambda obs: bool(obs["fact_delta"].data["new_facts"]),
        response="send_message",
        kind="action",
        params=lambda obs: {"to": str(child), "body": "look at the new lemma"},
    )
    profile = _profile([], [trigger], [send_message_actuator()])
    profile.sensors = [FactDeltaSensor(root)]

    summary = run_tick(root, profile, dry_run=True)
    assert summary.actions == [{"action": "send_message", "ok": True}]
    assert list_messages(child) == []  # nothing actually sent

    # Re-arm the sensor cursor and run for real.
    (root / ".iteris" / "supervision" / "cursors.json").write_text("{}", encoding="utf-8")
    run_tick(root, profile)
    assert len(list_messages(child, unread_only=True)) == 1


def test_run_loop_max_ticks_and_stop(root):
    profile = _profile([], [], [])
    profile.sensors = [StatusSensor(root)]
    slept: list[float] = []
    ticks = run_loop(root, profile, max_ticks=3, sleep=slept.append)
    assert ticks == 3 and slept == [1, 1]
    stops = iter([False, True])
    ticks = run_loop(root, profile, sleep=lambda s: None, should_stop=lambda: next(stops))
    assert ticks == 1


def test_failed_judgment_rolls_back_input_cursors(root):
    _add_fact(root, 1)
    profile = _profile([_report_contract()], [_new_fact_trigger()], [write_report_actuator()])
    profile.sensors = [FactDeltaSensor(root)]

    summary = run_tick(root, profile, backend_override=lambda p: "junk forever")
    assert summary.judgments == [{"contract": "summarize", "ok": False}]
    # Cursor was NOT advanced: the same delta re-fires next tick and succeeds.
    summary = run_tick(
        root, profile, backend_override=lambda p: json.dumps({"headline": "recovered"})
    )
    assert summary.fired == ["new_facts"]
    assert summary.judgments == [{"contract": "summarize", "ok": True}]
    # After success the cursor advances for real.
    assert run_tick(root, profile, backend_override=lambda p: "{}").idle


def test_last_agent_message_fallback(tmp_path):
    from iteris.supervision.contracts import _last_agent_message

    events = tmp_path / "codex.events.jsonl"
    events.write_text(
        "\n".join(
            [
                json.dumps({"type": "thread.started"}),
                json.dumps({"type": "item.completed",
                            "item": {"type": "agent_message", "text": "draft"}}),
                json.dumps({"type": "item.completed",
                            "item": {"type": "agent_message", "text": '{"a": 1}'}}),
                json.dumps({"type": "turn.completed"}),
            ]
        ),
        encoding="utf-8",
    )
    assert _last_agent_message(events) == '{"a": 1}'
    assert _last_agent_message(tmp_path / "missing.jsonl") is None


def test_extract_decision_json_variants():
    assert extract_decision_json('{"a": 1}') == {"a": 1}
    fenced = "Some prose.\n```json\n{\"a\": 2}\n```\nmore\n```json\n{\"a\": 3}\n```"
    assert extract_decision_json(fenced) == {"a": 3}
    with pytest.raises(ValueError):
        extract_decision_json("no json here")

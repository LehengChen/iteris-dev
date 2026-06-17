"""The supervision tick loop: observe -> detect -> judge -> act -> record.

The engine is deterministic plumbing; all judgment happens inside contracts
behind the pluggable backend. An idle tick (no trigger fires) costs zero model
calls. The engine is stateless across restarts: cursors and the journal carry
everything forward, and a tick that dies mid-way is reconciled by journaled
intent/outcome pairs on the next run.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from iteris.supervision.actions import ActionResult, Actuator
from iteris.supervision.contracts import (
    Action,
    Backend,
    JudgmentContract,
    agent_backend,
    invoke_contract,
)
from iteris.supervision.events import Observation, Sensor, SupervisionContext, TriggerRule
from iteris.supervision.journal import append_entry, load_cursors, save_cursors


@dataclass
class Profile:
    name: str
    sensors: list[Sensor]
    triggers: list[TriggerRule]
    contracts: list[JudgmentContract]
    actuators: list[Actuator]
    tick_seconds: int = 600
    # Resolves the executor from $ITERIS_EXECUTOR per call, so supervision
    # judgments inherit the run's backend (codex or claude).
    backend_factory: Callable[[Path, str], Backend] = agent_backend


@dataclass
class TickSummary:
    fired: list[str] = field(default_factory=list)
    judgments: list[dict[str, Any]] = field(default_factory=list)
    actions: list[dict[str, Any]] = field(default_factory=list)
    idle: bool = True


def _debounce_key(rule: TriggerRule) -> str:
    return f"debounce:{rule.name}"


def run_tick(
    root: Path,
    profile: Profile,
    *,
    extra: dict[str, Any] | None = None,
    backend_override: Backend | None = None,
    dry_run: bool = False,
) -> TickSummary:
    """Execute one full supervision tick against ``root``."""
    ctx = SupervisionContext(root=root, cursors=load_cursors(root), extra=extra or {})
    contracts = {contract.name: contract for contract in profile.contracts}
    actuators = {actuator.name: actuator for actuator in profile.actuators}
    summary = TickSummary()

    # 1. Observe (read-only). Cursor updates are tracked per sensor so a
    # failed judgment can roll back its inputs' cursors — otherwise a
    # transient judgment failure would permanently skip those deltas.
    observations: dict[str, Observation] = {}
    cursor_updates: dict[str, Any] = {}
    cursor_updates_by_sensor: dict[str, dict[str, Any]] = {}
    for sensor in profile.sensors:
        obs = sensor.observe(ctx)
        observations[obs.sensor] = obs
        updates = obs.data.get("cursor_update") or {}
        cursor_updates_by_sensor[obs.sensor] = dict(updates)
        cursor_updates.update(updates)

    # 2. Detect.
    pending: list[tuple[TriggerRule, dict[str, Any]]] = []
    for rule in profile.triggers:
        remaining = int(ctx.cursors.get(_debounce_key(rule), 0))
        if remaining > 0:
            cursor_updates[_debounce_key(rule)] = remaining - 1
            continue
        if rule.condition(observations):
            pending.append((rule, rule.params(observations)))
            if rule.debounce_ticks:
                cursor_updates[_debounce_key(rule)] = rule.debounce_ticks
    if pending:
        summary.idle = False
    summary.fired = [rule.name for rule, _ in pending]

    append_entry(
        root,
        entry_type="tick",
        payload={
            "profile": profile.name,
            "sensors": sorted(observations),
            "fired": summary.fired,
            "dry_run": dry_run,
        },
    )

    # 3-4. Judge then queue actions.
    queue: list[Action] = []
    for rule, params in pending:
        if rule.kind == "action":
            queue.append(Action(name=rule.response, params=params))
            continue
        contract = contracts.get(rule.response)
        if contract is None:
            append_entry(
                root,
                entry_type="judgment_failed",
                payload={"trigger": rule.name, "error": f"unknown contract: {rule.response}"},
            )
            continue
        backend = backend_override or profile.backend_factory(root, contract.name)
        result = invoke_contract(
            contract, observations, backend=backend, trigger_params=params
        )
        if not result.ok:
            append_entry(
                root,
                entry_type="judgment_failed",
                payload={
                    "trigger": rule.name,
                    "contract": contract.name,
                    "error": result.error,
                    "attempts": result.attempts,
                },
                agent_run=result.agent_run,
            )
            summary.judgments.append({"contract": contract.name, "ok": False})
            # Roll back this contract's input cursors so the missed deltas
            # re-fire on a later tick instead of being silently skipped.
            for sensor_name in contract.inputs:
                for key in cursor_updates_by_sensor.get(sensor_name, {}):
                    cursor_updates.pop(key, None)
            continue
        decision_entry = append_entry(
            root,
            entry_type="decision",
            payload={
                "trigger": rule.name,
                "contract": contract.name,
                "decision": result.decision,
                "attempts": result.attempts,
            },
            agent_run=result.agent_run,
        )
        summary.judgments.append({"contract": contract.name, "ok": True})
        for action in result.actions:
            action.decision_ref = decision_entry["entry_id"]
            queue.append(action)

    # 5. Act (intent before execute, outcome after), then persist cursors.
    for action in queue:
        actuator = actuators.get(action.name)
        if actuator is None:
            append_entry(
                root,
                entry_type="action_refused",
                payload={"action": action.name, "error": "no actuator in profile"},
            )
            continue
        intent = append_entry(
            root,
            entry_type="action_intent",
            payload={
                "action": action.name,
                "params": action.params,
                "decision_ref": action.decision_ref,
                "dry_run": dry_run,
            },
        )
        if dry_run:
            result_detail: dict[str, Any] = {"dry_run": True}
            ok = True
        else:
            outcome: ActionResult = actuator.execute(action, ctx)
            result_detail = outcome.detail
            ok = outcome.ok
        append_entry(
            root,
            entry_type="action_outcome",
            payload={"action": action.name, "ok": ok, "detail": result_detail},
            supersedes=intent["entry_id"],
        )
        summary.actions.append({"action": action.name, "ok": ok})

    ctx.cursors.update(cursor_updates)
    save_cursors(root, ctx.cursors)
    return summary


def run_loop(
    root: Path,
    profile: Profile,
    *,
    extra: dict[str, Any] | None = None,
    backend_override: Backend | None = None,
    dry_run: bool = False,
    max_ticks: int | None = None,
    sleep: Callable[[float], None] = time.sleep,
    should_stop: Callable[[], bool] = lambda: False,
    on_tick: Callable[[int, TickSummary], None] | None = None,
) -> int:
    """Run ticks until stopped. Returns the number of ticks executed.

    ``on_tick`` receives (tick_number, summary) after each tick — the
    foreground CLI uses it to print a heartbeat so a watching terminal can
    tell "alive and idle" from "dead".
    """
    ticks = 0
    while not should_stop():
        summary = run_tick(
            root,
            profile,
            extra=extra,
            backend_override=backend_override,
            dry_run=dry_run,
        )
        ticks += 1
        if on_tick is not None:
            on_tick(ticks, summary)
        if max_ticks is not None and ticks >= max_ticks:
            break
        sleep(profile.tick_seconds)
    return ticks

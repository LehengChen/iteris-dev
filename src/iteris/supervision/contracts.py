"""Judgment contracts: bounded agent calls with validated, allowlisted output.

A contract declares its prompt template, a validator for the decision JSON,
the allowlist of action names its decisions may trigger, and an adapter that
maps a validated decision to concrete actions. Invocation sediments raw
evidence through the existing agent-run machinery (``artifacts/agent_runs/``,
role ``judge-<contract>``), the same discipline as verification agents.

Backends are pluggable: the default runs Codex headlessly; tests inject a
callable returning canned JSON, which keeps the whole engine hermetic.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from iteris.supervision.events import Observation

MAX_ATTEMPTS = 3

Validator = Callable[[dict[str, Any]], list[str]]
Backend = Callable[[str], str]
ActionAdapter = Callable[[dict[str, Any]], list["Action"]]


@dataclass
class Action:
    name: str
    params: dict[str, Any] = field(default_factory=dict)
    decision_ref: str | None = None


@dataclass
class JudgmentContract:
    """A bounded judgment task.

    ``inputs`` lists the sensor names whose CURRENT observations are injected.
    Prior decisions are never inputs; debounce history is the engine's
    business, not the contract's.
    """

    name: str
    inputs: list[str]
    prompt_template: str  # str.format(inputs_json=..., retry_feedback=...)
    validator: Validator
    allowed_actions: list[str]
    to_actions: ActionAdapter


@dataclass
class JudgmentResult:
    ok: bool
    contract: str
    decision: dict[str, Any] | None = None
    actions: list[Action] = field(default_factory=list)
    error: str | None = None
    attempts: int = 0
    agent_run: str | None = None


_JSON_BLOCK_RE = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.DOTALL)


def extract_decision_json(raw: str) -> dict[str, Any]:
    """Parse the decision object from a backend reply.

    Accepts a bare JSON object or the last fenced JSON block in a longer
    reply (headless Codex tends to wrap output in markdown).
    """
    text = raw.strip()
    candidates: list[str] = []
    if text.startswith("{"):
        candidates.append(text)
    candidates.extend(match for match in _JSON_BLOCK_RE.findall(raw))
    for candidate in reversed(candidates):
        try:
            parsed = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            return parsed
    raise ValueError("no JSON object found in backend reply")


def render_prompt(
    contract: JudgmentContract,
    observations: dict[str, Observation],
    *,
    trigger_params: dict[str, Any] | None = None,
    retry_feedback: str = "",
) -> str:
    inputs = {
        name: observations[name].data for name in contract.inputs if name in observations
    }
    if trigger_params:
        inputs["trigger"] = trigger_params
    inputs_json = json.dumps(inputs, indent=2, ensure_ascii=False, sort_keys=True, default=str)
    return contract.prompt_template.format(
        inputs_json=inputs_json, retry_feedback=retry_feedback
    )


def _backend_run_id(backend: Backend) -> str | None:
    """Backends that sediment raw runs expose the last run id for audit refs."""
    return getattr(backend, "last_run_id", None)


def invoke_contract(
    contract: JudgmentContract,
    observations: dict[str, Observation],
    *,
    backend: Backend,
    trigger_params: dict[str, Any] | None = None,
    agent_run: str | None = None,
) -> JudgmentResult:
    """Run a contract through validate-retry. Never raises on bad output:
    a contract that cannot produce a valid decision yields ``ok=False`` and
    the engine journals ``judgment_failed`` — silence is not consent."""
    retry_feedback = ""
    last_error = "no attempts made"
    for attempt in range(1, MAX_ATTEMPTS + 1):
        prompt = render_prompt(
            contract, observations, trigger_params=trigger_params, retry_feedback=retry_feedback
        )
        try:
            raw = backend(prompt)
            decision = extract_decision_json(raw)
        except Exception as exc:  # backend or parse failure
            last_error = f"attempt {attempt}: {exc}"
            retry_feedback = (
                f"\n\nYour previous reply was invalid: {exc}. "
                "Reply with ONLY one JSON object conforming to the output contract."
            )
            continue
        # Mechanical context for adapters: trigger params must not depend on
        # the model echoing them back.
        decision["_trigger"] = trigger_params or {}
        errors = contract.validator(decision)
        if errors:
            last_error = f"attempt {attempt}: validation failed: {'; '.join(errors)}"
            retry_feedback = (
                "\n\nYour previous JSON failed validation: "
                + "; ".join(errors)
                + ". Reply with ONLY one corrected JSON object."
            )
            continue
        actions = contract.to_actions(decision)
        stray = [a.name for a in actions if a.name not in contract.allowed_actions]
        if stray:
            # Adapter bug or malicious decision shape — refuse, do not retry.
            return JudgmentResult(
                ok=False,
                contract=contract.name,
                decision=decision,
                error=f"actions outside allowlist: {stray}",
                attempts=attempt,
                agent_run=_backend_run_id(backend) or agent_run,
            )
        return JudgmentResult(
            ok=True,
            contract=contract.name,
            decision=decision,
            actions=actions,
            attempts=attempt,
            agent_run=_backend_run_id(backend) or agent_run,
        )
    return JudgmentResult(
        ok=False, contract=contract.name, error=last_error, attempts=MAX_ATTEMPTS, agent_run=_backend_run_id(backend) or agent_run
    )


def agent_backend(project_root: Path, contract_name: str, *, executor: str | None = None) -> Backend:
    """Default production backend: one foreground headless agent run per call.

    Sediments prompt, raw event stream, and output under
    ``artifacts/agent_runs/judge-<contract>-...`` via the standard agent-run
    machinery; returns the run's markdown/JSON output as the reply text. The
    executor (codex or claude) is inherited from $ITERIS_EXECUTOR when not given,
    so supervision judgments run on the same backend as the rest of the run.
    """
    from iteris.agents.runtime import create_agent_run
    from iteris.executors import resolve_executor

    resolved = resolve_executor(executor)

    def _run(prompt: str) -> str:
        summary = create_agent_run(
            project_root,
            role=f"judge-{contract_name}",
            prompt_builder=lambda request: prompt,
            detached=False,
            executor=resolved,
        )
        _run.last_run_id = summary["run_id"]  # audit ref for the journal
        run_dir = project_root / "artifacts" / "agent_runs" / summary["run_id"]
        output_json = run_dir / "output.json"
        if output_json.exists():
            return output_json.read_text(encoding="utf-8", errors="replace")
        output_md = run_dir / "output.md"
        if output_md.exists():
            return output_md.read_text(encoding="utf-8", errors="replace")
        # Judgment prompts say "reply with ONLY one JSON object", so the
        # decision usually arrives as the final agent message rather than the
        # standard output files — pull it from the raw event stream. The events
        # file name is codex.events.jsonl for both executors (it just holds
        # claude stream-json when executor=claude); the reader handles both.
        reply = _last_agent_message(run_dir / "codex.events.jsonl")
        if reply:
            return reply
        raise RuntimeError(f"judgment run produced no output: {summary['run_id']}")

    return _run


# Back-compat alias: the default was named codex_backend before multi-executor
# support. Forces codex so callers that asked for it by name still get codex.
def codex_backend(project_root: Path, contract_name: str) -> Backend:
    return agent_backend(project_root, contract_name, executor="codex")


def _last_agent_message(events_path: Path) -> str | None:
    """Last assistant reply from a headless event stream (codex or claude).

    Handles codex's two event shapes (item.type==agent_message; bare
    type==agent_message) and claude's stream-json (the final ``result`` event's
    ``result`` text, else the last ``assistant`` message's text blocks).
    """
    if not events_path.exists():
        return None
    last: str | None = None
    for line in events_path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(event, dict):
            continue
        item = event.get("item")
        etype = event.get("type")
        if isinstance(item, dict) and item.get("type") == "agent_message" and item.get("text"):
            last = str(item["text"])
        elif etype == "agent_message" and event.get("text"):
            last = str(event["text"])
        elif etype == "result" and event.get("result"):
            last = str(event["result"])
        elif etype == "assistant":
            message = event.get("message")
            if isinstance(message, dict):
                texts = [
                    str(part.get("text"))
                    for part in message.get("content") or []
                    if isinstance(part, dict) and part.get("type") == "text" and part.get("text")
                ]
                if texts:
                    last = "\n".join(texts)
    return last

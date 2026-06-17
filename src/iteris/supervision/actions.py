"""Actuators — the only side-effecting layer of the supervision engine."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Protocol

from iteris.supervision.contracts import Action


@dataclass
class ActionResult:
    action: str
    ok: bool
    detail: dict[str, Any]


class Actuator(Protocol):
    name: str

    def execute(self, action: Action, ctx: "SupervisionContext") -> ActionResult: ...  # noqa: F821


@dataclass
class CallableActuator:
    """Wrap a plain function as an actuator."""

    name: str
    fn: Callable[[Action, Any], dict[str, Any]]

    def execute(self, action: Action, ctx: Any) -> ActionResult:
        try:
            detail = self.fn(action, ctx)
            return ActionResult(action=action.name, ok=True, detail=detail or {})
        except Exception as exc:
            return ActionResult(action=action.name, ok=False, detail={"error": str(exc)})


def send_message_actuator() -> CallableActuator:
    """Generic L2 steering: append a structured message to a target project."""

    def _send(action: Action, ctx: Any) -> dict[str, Any]:
        from iteris.messages import send

        target = Path(action.params["to"]).resolve()
        message = send(
            target,
            body=action.params["body"],
            type=action.params.get("type", "nudge"),
            priority=action.params.get("priority", "normal"),
            sender="supervisor",
            refs=action.params.get("refs") or [],
        )
        return {"msg_id": message["msg_id"], "to": str(target)}

    return CallableActuator(name="send_message", fn=_send)


def write_report_actuator() -> CallableActuator:
    """Render the rolling REPORT.md from the journal and provided sections."""

    def _write(action: Action, ctx: Any) -> dict[str, Any]:
        from iteris.supervision.report import render_report

        path = render_report(
            ctx.root,
            headline=action.params.get("headline", ""),
            body_markdown=action.params.get("report_markdown", ""),
            health=action.params.get("health", ""),
        )
        return {"path": str(path)}

    return CallableActuator(name="write_report", fn=_write)


def record_actuator() -> CallableActuator:
    """No-op beyond the journal: for observations worth flagging without action."""

    def _record(action: Action, ctx: Any) -> dict[str, Any]:
        return dict(action.params)

    return CallableActuator(name="record", fn=_record)

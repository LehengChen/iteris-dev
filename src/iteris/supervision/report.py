"""Rolling REPORT.md renderer.

REPORT.md is a rendered view for humans and the dashboard — freely
overwritten, never a source of truth. Immutable stage reports live under
``artifacts/reports/`` and are produced by the ``write_stage_report``
contract, not here.
"""

from __future__ import annotations

from pathlib import Path

from iteris.project import now_iso
from iteris.supervision.journal import read_entries, supervision_dir


def render_report(
    project_root: Path,
    *,
    headline: str = "",
    body_markdown: str = "",
    health: str = "",
    journal_tail: int = 20,
) -> Path:
    lines: list[str] = [f"# Supervision report — {project_root.name}", ""]
    meta = [f"updated: {now_iso()}"]
    if health:
        meta.append(f"health: {health}")
    lines.extend(meta)
    lines.append("")
    if headline:
        lines.extend([f"**{headline}**", ""])
    if body_markdown:
        lines.extend([body_markdown.strip(), ""])
    recent = read_entries(project_root, limit=journal_tail)
    if recent:
        lines.extend(["## Recent activity", ""])
        for entry in recent:
            kind = entry.get("entry_type")
            if kind == "tick" and not entry.get("payload", {}).get("fired"):
                continue  # idle ticks are noise in a human report
            ts = str(entry.get("ts", ""))[:19]
            payload = entry.get("payload", {})
            if kind == "decision":
                detail = f"{payload.get('contract')} (trigger {payload.get('trigger')})"
            elif kind in {"action_intent", "action_outcome"}:
                detail = f"{payload.get('action')}" + (
                    "" if payload.get("ok", True) else " FAILED"
                )
            elif kind == "judgment_failed":
                detail = f"{payload.get('contract') or payload.get('trigger')}: {payload.get('error')}"
            else:
                detail = ", ".join(str(v) for v in payload.get("fired", [])) or kind
            lines.append(f"- `{ts}` {kind}: {detail}")
        lines.append("")
    path = supervision_dir(project_root) / "REPORT.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")
    return path

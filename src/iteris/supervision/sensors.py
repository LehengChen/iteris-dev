"""Generic read-only sensors shared by supervision profiles.

Each sensor observes one project directory. Profiles that watch many projects
(evolve) instantiate one sensor per node or aggregate in a profile-specific
sensor. Sensors may READ cursors to compute deltas but must not write them —
the engine advances cursors after a successful tick.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

from iteris.project import read_json
from iteris.supervision.events import Observation, SupervisionContext


def _status_fields(project_root: Path) -> dict[str, str]:
    path = project_root / "STATUS.md"
    fields: dict[str, str] = {}
    if not path.exists():
        return fields
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if ":" in line and not line.startswith((" ", "\t", "-")):
            key, value = line.split(":", 1)
            key = key.strip()
            if key and " " not in key:
                fields[key] = value.strip()
    return fields


@dataclass
class StatusSensor:
    """STATUS.md phase + key fields + change detection vs cursor."""

    project: Path
    name: str = "status"

    def observe(self, ctx: SupervisionContext) -> Observation:
        fields = _status_fields(self.project)
        text = (self.project / "STATUS.md").read_text(encoding="utf-8", errors="replace") if (
            self.project / "STATUS.md"
        ).exists() else ""
        digest = hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]
        previous = ctx.cursors.get(f"{self.name}:digest")
        return Observation(
            sensor=self.name,
            data={
                "project": str(self.project),
                "phase": fields.get("phase"),
                "fields": fields,
                "digest": digest,
                "changed": previous is not None and previous != digest,
                "cursor_update": {f"{self.name}:digest": digest},
            },
        )


@dataclass
class FactDeltaSensor:
    """New FACT_INDEX.jsonl lines since the cursor."""

    project: Path
    name: str = "fact_delta"

    def observe(self, ctx: SupervisionContext) -> Observation:
        import json

        index = self.project / "memory" / "facts" / "FACT_INDEX.jsonl"
        lines = (
            [l for l in index.read_text(encoding="utf-8", errors="replace").splitlines() if l.strip()]
            if index.exists()
            else []
        )
        seen = int(ctx.cursors.get(f"{self.name}:lines", 0))
        fresh: list[dict] = []
        for line in lines[seen:]:
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(row, dict):
                fresh.append(row)
        return Observation(
            sensor=self.name,
            data={
                "project": str(self.project),
                "total": len(lines),
                "new_facts": fresh,
                "new_verified": [row for row in fresh if row.get("status") == "verified"],
                "cursor_update": {f"{self.name}:lines": len(lines)},
            },
        )


def tmux_session_alive(session_name: str) -> bool:
    from iteris.tmux import tmux_session_alive as _alive

    return _alive(session_name)


@dataclass
class SessionSensor:
    """Liveness of the project's worker run, from current_run.json + tmux."""

    project: Path
    name: str = "session"

    def observe(self, ctx: SupervisionContext) -> Observation:
        current = read_json(self.project / ".iteris" / "current_run.json", default={})
        session = current.get("session_name")
        alive = bool(session) and tmux_session_alive(str(session))
        return Observation(
            sensor=self.name,
            data={
                "project": str(self.project),
                "session": session,
                "alive": alive,
                "has_run": bool(current),
            },
        )

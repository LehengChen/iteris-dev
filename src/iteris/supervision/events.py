"""Observation and trigger types for the supervision engine."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Protocol


@dataclass
class SupervisionContext:
    """Everything a tick needs. ``cursors`` is mutated in place and persisted
    by the engine after actuators run; ``extra`` carries profile-specific
    configuration (e.g. the evolve node list)."""

    root: Path
    cursors: dict[str, Any] = field(default_factory=dict)
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass
class Observation:
    sensor: str
    data: dict[str, Any]


class Sensor(Protocol):
    """Read-only observer. MUST NOT write anything anywhere."""

    name: str

    def observe(self, ctx: SupervisionContext) -> Observation: ...


@dataclass
class TriggerRule:
    """Declarative mapping from the current observations to a response.

    ``response`` names either a judgment contract or a direct action known to
    the profile. ``condition`` sees the full observation map so rules can
    correlate sensors. ``params`` extracts the action/contract input payload
    from the observations when the rule fires. ``debounce_ticks`` suppresses
    re-firing for N subsequent ticks after a firing (tracked via cursors).
    """

    name: str
    condition: Callable[[dict[str, Observation]], bool]
    response: str
    kind: str = "contract"  # "contract" | "action"
    params: Callable[[dict[str, Observation]], dict[str, Any]] = lambda obs: {}
    debounce_ticks: int = 0

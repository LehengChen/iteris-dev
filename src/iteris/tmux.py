"""Pure tmux session primitives.

Self-contained subprocess wrappers shared by the run/goal commands, the
liveness scanner, and the evolve supervisor. Command modules re-export these
names for backward compatibility; new code should import from here.
"""

from __future__ import annotations

import os
import shlex
import subprocess
import time
from pathlib import Path


def tmux_session_exists(session_name: str) -> bool:
    try:
        result = subprocess.run(
            ["tmux", "has-session", "-t", session_name],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
    except FileNotFoundError as exc:
        raise RuntimeError("tmux is not installed") from exc
    return result.returncode == 0


def tmux_session_alive(session_name: str) -> bool:
    """Like ``tmux_session_exists`` but treats a missing tmux as not-alive."""
    if not session_name:
        return False
    try:
        return tmux_session_exists(session_name)
    except RuntimeError:
        return False


def capture_pane(session_name: str, *, lines: int = 200) -> str:
    try:
        result = subprocess.run(
            ["tmux", "capture-pane", "-pt", session_name, "-S", f"-{lines}"],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
    except FileNotFoundError as exc:
        raise RuntimeError("tmux is not installed") from exc
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or f"tmux session not found: {session_name}")
    return result.stdout


def build_pipe_pane_command(session_name: str, pane_log: Path) -> list[str]:
    return ["tmux", "pipe-pane", "-o", "-t", session_name, f"cat >> {shlex.quote(str(pane_log))}"]


def build_interrupt_command(session_name: str) -> list[str]:
    return ["tmux", "send-keys", "-t", session_name, "C-c"]


def build_kill_session_command(session_name: str) -> list[str]:
    return ["tmux", "kill-session", "-t", session_name]


def tmux_attach_command(session_name: str, *, env: dict[str, str] | None = None) -> list[str]:
    source_env = os.environ if env is None else env
    if source_env.get("TMUX"):
        return ["tmux", "switch-client", "-t", session_name]
    return ["tmux", "attach-session", "-t", session_name]


def attach_tmux_session(session_name: str) -> None:
    command = tmux_attach_command(session_name)
    result = subprocess.run(command, check=False)
    if result.returncode == 0:
        return
    if command[1] == "switch-client":
        hint = f"failed to switch tmux client to {session_name}"
    else:
        hint = f"failed to attach to tmux session {session_name}"
    raise RuntimeError(f"{hint}. Inspect the run with `iteris dashboard` or `iteris status --session {session_name}`.")


def stop_tmux_session(
    session_name: str,
    *,
    force_after: float = 5.0,
    force: bool = True,
    poll_interval: float = 0.25,
) -> dict[str, object]:
    actions: list[str] = []
    if not tmux_session_exists(session_name):
        return {"stopped": False, "active": False, "reason": "session not found", "actions": actions}

    subprocess.run(build_interrupt_command(session_name), check=False)
    actions.append("interrupt")

    deadline = time.monotonic() + max(force_after, 0.0)
    while time.monotonic() < deadline:
        if not tmux_session_exists(session_name):
            return {"stopped": True, "active": False, "reason": "stopped after interrupt", "actions": actions}
        time.sleep(min(poll_interval, max(deadline - time.monotonic(), 0.0)))

    if not tmux_session_exists(session_name):
        return {"stopped": True, "active": False, "reason": "stopped after interrupt", "actions": actions}

    if not force:
        return {"stopped": False, "active": True, "reason": "session still active after interrupt", "actions": actions}

    subprocess.run(build_kill_session_command(session_name), check=False)
    actions.append("kill-session")
    active = tmux_session_exists(session_name)
    return {
        "stopped": not active,
        "active": active,
        "reason": "forced tmux session stop" if not active else "failed to stop tmux session",
        "actions": actions,
    }

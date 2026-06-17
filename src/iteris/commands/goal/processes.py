"""Discovery and termination of a project's verification-agent processes.

Identifies Iteris verification-agent processes (codex or claude) by the
ITERIS_PROCESS_ROLE / ITERIS_PROJECT_ROOT env tags (plus a legacy command-line
match for old codex runs) so `iteris stop` can reap them. Worker/execution
draining lives in iteris.agents.runtime; this module is verifier-specific.
"""

from __future__ import annotations

import os
import signal
import subprocess
import time
from pathlib import Path
from typing import Callable


def verifier_processes(project_root: Path) -> list[dict[str, object]]:
    """Find Iteris verification-agent processes (codex or claude) for this project."""
    root_text = str(project_root.resolve())
    try:
        result = subprocess.run(
            ["ps", "-eo", "pid=,ppid=,cmd="],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
    except FileNotFoundError:
        return []
    if result.returncode != 0:
        return []
    return _matching_verifier_processes(result.stdout, root_text, env_by_pid=_process_env_by_pid)


def _matching_verifier_processes(
    ps_output: str,
    root_text: str,
    *,
    env_by_pid: Callable[[int], str] | None = None,
) -> list[dict[str, object]]:
    matches: list[dict[str, object]] = []
    for line in ps_output.splitlines():
        parts = line.strip().split(None, 2)
        if len(parts) != 3:
            continue
        pid_text, ppid_text, cmd = parts
        # Cheap pre-filter so we only read /proc env for plausible candidates:
        # a codex headless exec, or a claude headless print (`claude -p
        # --output-format stream-json`, which carries no -C <root> in argv).
        is_codex = "codex exec" in cmd
        is_claude = "claude" in cmd and "stream-json" in cmd
        if not (is_codex or is_claude):
            continue
        try:
            pid = int(pid_text)
            ppid = int(ppid_text)
        except ValueError:
            continue
        role_env = env_by_pid(pid) if env_by_pid else ""
        tagged_verifier = "ITERIS_PROCESS_ROLE=verification_agent" in role_env
        # Claude headless carries no project root in argv, so it is matched by
        # the env tags verify_agent sets (role + project root).
        env_tagged = tagged_verifier and f"ITERIS_PROJECT_ROOT={root_text}" in role_env
        # Codex headless: project root is in argv (-C <root>); match the stdin
        # form by argv + the role tag, same as before claude support.
        codex_stdin = (
            is_codex
            and "--dangerously-bypass-approvals-and-sandbox -" in cmd
            and root_text in cmd
            and tagged_verifier
        )
        # Legacy codex runs that passed the prompt as a command-line argument.
        old_prompt_arg = is_codex and "Iteris Verification Agent" in cmd and root_text in cmd
        if not (env_tagged or codex_stdin or old_prompt_arg):
            continue
        if pid == os.getpid():
            continue
        matches.append({"pid": pid, "ppid": ppid, "cmd": cmd})
    return matches


def _process_env_by_pid(pid: int) -> str:
    try:
        raw = Path(f"/proc/{pid}/environ").read_bytes()
    except Exception:
        return ""
    return raw.replace(b"\x00", b"\n").decode("utf-8", errors="replace")


def stop_verification_agents(
    project_root: Path,
    *,
    force_after: float = 5.0,
    force: bool = True,
    poll_interval: float = 0.25,
) -> dict[str, object]:
    initial = verifier_processes(project_root)
    actions: list[dict[str, object]] = []
    for proc in initial:
        pid = int(proc["pid"])
        try:
            os.kill(pid, signal.SIGTERM)
            actions.append({"pid": pid, "signal": "TERM"})
        except ProcessLookupError:
            actions.append({"pid": pid, "signal": "TERM", "status": "already exited"})
        except PermissionError as exc:
            actions.append({"pid": pid, "signal": "TERM", "status": str(exc)})

    deadline = time.monotonic() + max(force_after, 0.0)
    remaining = verifier_processes(project_root)
    while remaining and time.monotonic() < deadline:
        time.sleep(min(poll_interval, max(deadline - time.monotonic(), 0.0)))
        remaining = verifier_processes(project_root)

    if remaining and force:
        for proc in remaining:
            pid = int(proc["pid"])
            try:
                os.kill(pid, signal.SIGKILL)
                actions.append({"pid": pid, "signal": "KILL"})
            except ProcessLookupError:
                actions.append({"pid": pid, "signal": "KILL", "status": "already exited"})
            except PermissionError as exc:
                actions.append({"pid": pid, "signal": "KILL", "status": str(exc)})
        time.sleep(poll_interval)
        remaining = verifier_processes(project_root)

    return {
        "initial": initial,
        "actions": actions,
        "remaining": remaining,
        "stopped": not remaining,
    }

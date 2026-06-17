"""Codex session plumbing: command/tmux builders, codex-home + trust setup, prompt readiness."""

from __future__ import annotations

import json
import os
import re
import shlex
import subprocess
import time

from iteris.gitops import ensure_gitignore
from iteris.project import slugify
from iteris.tmux import capture_pane
from pathlib import Path
from typing import Sequence
from iteris.commands.goal.logs import _mtime_or_zero


def build_codex_command(
    root: Path,
    prompt: str | None = None,
    *,
    executable: str = "codex",
    yolo: bool = True,
    no_alt_screen: bool = True,
    model: str | None = None,
    prompt_argument: bool = True,
) -> list[str]:
    cmd = [executable]
    if yolo:
        cmd.append("--yolo")
    if no_alt_screen:
        cmd.append("--no-alt-screen")
    if model:
        cmd.extend(["-m", model])
    cmd.extend(["--cd", str(root)])
    if prompt is not None and prompt_argument:
        cmd.append(prompt)
    return cmd


def codex_project_trust_section(project_root: Path) -> str:
    return f"[projects.{json.dumps(str(project_root.resolve()), ensure_ascii=False)}]"


def codex_home(env: dict[str, str] | None = None) -> Path:
    source_env = os.environ if env is None else env
    return Path(source_env.get("CODEX_HOME") or Path.home() / ".codex").expanduser()


def ensure_codex_project_trusted(project_root: Path, *, env: dict[str, str] | None = None) -> dict[str, object]:
    """Persist Codex's project trust decision for automated Iteris launches."""
    root = project_root.resolve()
    config_path = codex_home(env) / "config.toml"
    section = codex_project_trust_section(root)
    result: dict[str, object] = {
        "config_path": str(config_path),
        "project_path": str(root),
        "section": section,
        "already_trusted": False,
        "updated": False,
    }
    text = config_path.read_text(encoding="utf-8", errors="replace") if config_path.exists() else ""
    section_pattern = re.compile(rf"(?ms)^{re.escape(section)}\s*$.*?(?=^\[|\Z)")
    match = section_pattern.search(text)
    if match:
        block = match.group(0)
        if re.search(r'(?m)^\s*trust_level\s*=\s*"trusted"\s*$', block):
            result["already_trusted"] = True
            return result
        if re.search(r"(?m)^\s*trust_level\s*=", block):
            new_block = re.sub(r'(?m)^(\s*trust_level\s*=\s*).+$', r'\1"trusted"', block, count=1)
        else:
            new_block = block.rstrip() + '\ntrust_level = "trusted"\n'
        text = text[: match.start()] + new_block + text[match.end() :]
    else:
        prefix = "\n\n" if text and not text.endswith("\n\n") else ""
        text = text + prefix + section + '\ntrust_level = "trusted"\n'
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(text, encoding="utf-8")
    result["updated"] = True
    return result


def build_tmux_command(session_name: str, shell_cmd: str, *, detached: bool = False) -> list[str]:
    cmd = ["tmux", "new-session"]
    if detached:
        cmd.append("-d")
    cmd.extend(["-s", session_name, shell_cmd])
    return cmd


def build_tmux_shell_command(session_name: str) -> list[str]:
    return ["tmux", "new-session", "-d", "-s", session_name]


def build_send_keys_command(session_name: str, shell_cmd: str) -> list[str]:
    return ["tmux", "send-keys", "-t", session_name, shell_cmd, "Enter"]


def build_load_prompt_buffer_command(session_name: str, prompt_path: Path, *, buffer_name: str = "iteris_goal_prompt") -> list[str]:
    return ["tmux", "load-buffer", "-t", session_name, "-b", buffer_name, str(prompt_path)]


def build_paste_prompt_buffer_command(session_name: str, *, buffer_name: str = "iteris_goal_prompt") -> list[str]:
    return ["tmux", "paste-buffer", "-t", session_name, "-b", buffer_name]


def build_submit_prompt_command(session_name: str) -> list[str]:
    return ["tmux", "send-keys", "-t", session_name, "Enter"]


_CODEX_HOME_LINK_FILES = ("auth.json", "config.toml", "version.json", "installation_id")


def goal_codex_home_dir(root: Path, session_name: str, stamp: str) -> Path:
    """Per-run CODEX_HOME directory, name-correlated with build_goal_log_paths."""
    safe_session = slugify(session_name, 60)
    return root / ".iteris" / "codex_home" / f"goal-{safe_session}-{stamp}"


def prepare_codex_home(root: Path, session_name: str, stamp: str) -> Path:
    """Create the per-run CODEX_HOME and symlink auth/config from the real one.

    Codex writes its structured rollout to $CODEX_HOME/sessions/...; pointing
    CODEX_HOME inside the project captures it there. Auth/config are symlinked
    (never copied into the project) so login keeps working; the source is the
    user's real Codex home (respecting $CODEX_HOME, falling back to ~/.codex).
    Idempotent; if a source file is missing (e.g. fresh machine never logged
    in) it is skipped and Codex will create its own.

    Caveat: if Codex rewrites a linked file via rename (rather than in-place),
    the symlink is replaced by a real file in the per-run dir and the copies
    diverge. prune_goal_runs deletes old per-run dirs, which bounds how long
    any such stray copy lives.
    """
    home_dir = goal_codex_home_dir(root, session_name, stamp)
    home_dir.mkdir(parents=True, exist_ok=True)
    # The dir holds auth symlinks and rollout logs; make sure it is ignored
    # even in projects created before .iteris/codex_home/ entered the default
    # gitignore (checkpoint commits would otherwise pick it up).
    try:
        ensure_gitignore(root)
    except OSError:
        pass
    source_root = codex_home()
    for name in _CODEX_HOME_LINK_FILES:
        source = source_root / name
        link = home_dir / name
        if link.exists() or link.is_symlink():
            continue
        if not source.exists():
            continue
        try:
            link.symlink_to(source)
        except OSError:
            pass
    return home_dir


def find_run_rollout(codex_home_dir: Path) -> Path | None:
    """Newest rollout JSONL under a per-run CODEX_HOME, or None if not yet written."""
    sessions = codex_home_dir / "sessions"
    if not sessions.is_dir():
        return None
    rollouts = sorted(sessions.glob("**/rollout-*.jsonl"), key=_mtime_or_zero, reverse=True)
    return rollouts[0] if rollouts else None


def codex_prompt_ready(pane_text: str) -> bool:
    return "›" in pane_text and ("Use /skills" in pane_text or "Tip:" in pane_text or "gpt-" in pane_text)


def codex_trust_prompt_present(pane_text: str) -> bool:
    return "Do you trust the contents of this directory" in pane_text and (
        "Press enter to continue" in pane_text or "Yes, continue" in pane_text
    )


def prepare_codex_prompt(
    session_name: str,
    *,
    timeout_seconds: float = 60.0,
    poll_interval: float = 0.5,
    auto_accept_trust: bool = True,
) -> dict[str, object]:
    deadline = time.monotonic() + max(timeout_seconds, 0.0)
    trust_accepted = False
    while time.monotonic() < deadline:
        try:
            pane_text = capture_pane(session_name, lines=100)
        except RuntimeError:
            return {"ready": False, "trust_accepted": trust_accepted}
        if auto_accept_trust and codex_trust_prompt_present(pane_text):
            subprocess.run(build_submit_prompt_command(session_name), check=False)
            trust_accepted = True
            time.sleep(1.0)
            continue
        if codex_prompt_ready(pane_text):
            time.sleep(0.5)
            return {"ready": True, "trust_accepted": trust_accepted}
        time.sleep(poll_interval)
    return {"ready": False, "trust_accepted": trust_accepted}


def wait_for_codex_prompt(session_name: str, *, timeout_seconds: float = 30.0, poll_interval: float = 0.5) -> bool:
    return bool(prepare_codex_prompt(session_name, timeout_seconds=timeout_seconds, poll_interval=poll_interval, auto_accept_trust=False)["ready"])


def accept_codex_trust_prompt(
    session_name: str,
    *,
    timeout_seconds: float = 15.0,
    poll_interval: float = 0.5,
    auto_accept_trust: bool = True,
) -> dict[str, object]:
    deadline = time.monotonic() + max(timeout_seconds, 0.0)
    observed = False
    accepted = False
    while time.monotonic() < deadline:
        try:
            pane_text = capture_pane(session_name, lines=80)
        except RuntimeError:
            return {"observed": observed, "accepted": accepted}
        if codex_trust_prompt_present(pane_text):
            observed = True
            if auto_accept_trust:
                subprocess.run(build_submit_prompt_command(session_name), check=False)
                accepted = True
                time.sleep(1.0)
                continue
            return {"observed": observed, "accepted": accepted}
        if "OpenAI Codex" in pane_text or "Goal active" in pane_text or "Working" in pane_text:
            return {"observed": observed, "accepted": accepted}
        time.sleep(poll_interval)
    return {"observed": observed, "accepted": accepted}


def build_shell_command(
    root: Path,
    codex_cmd: Sequence[str],
    prompt: str | None = None,
    *,
    prompt_file_argument: bool = False,
    codex_home: Path | None = None,
    env_updates: dict[str, str] | None = None,
) -> str:
    prompt_path = root / ".iteris" / "goal_prompt.txt"
    # `exec env KEY=value ...` pins the executor's per-run state dir (and any
    # other launch env) in the command string itself, which is robust
    # regardless of how `tmux respawn-pane` inherits the environment.
    env_pairs: dict[str, str] = {}
    if codex_home is not None:
        env_pairs["CODEX_HOME"] = str(codex_home)
    if env_updates:
        env_pairs.update(env_updates)
    exec_prefix = "exec"
    if env_pairs:
        assignments = " ".join(f"{key}={shlex.quote(value)}" for key, value in env_pairs.items())
        exec_prefix = f"exec env {assignments}"
    if prompt_file_argument:
        prompt_rel = shlex.quote(str(prompt_path.relative_to(root)))
        return f"cd {shlex.quote(str(root))} && {exec_prefix} {shlex.join(list(codex_cmd))} \"$(cat {prompt_rel})\""
    if prompt is None:
        return f"cd {shlex.quote(str(root))} && {exec_prefix} {shlex.join(list(codex_cmd))}"
    return (
        f"cd {shlex.quote(str(root))} && "
        f"printf '%s\\n' {shlex.quote(prompt)} > {shlex.quote(str(prompt_path.relative_to(root)))} && "
        f"{exec_prefix} {shlex.join(list(codex_cmd))}"
    )

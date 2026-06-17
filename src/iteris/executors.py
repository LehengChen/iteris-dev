"""Executor (agent CLI backend) selection for Iteris-launched agents.

Iteris launches LLM agents through external CLIs. Two executors are
supported:

- ``codex``: the OpenAI Codex CLI (``codex --yolo ...``).
- ``claude``: the Claude Code CLI (``IS_SANDBOX=1 claude
  --dangerously-skip-permissions ...``).

This module owns the executor-generic pieces (resolution, the Claude
command/home machinery, env construction). The Codex-specific helpers
remain in ``iteris.commands.goal`` and ``iteris.agents.runtime``; callers
dispatch on the resolved executor name.
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

EXECUTOR_CODEX = "codex"
EXECUTOR_CLAUDE = "claude"
KNOWN_EXECUTORS = (EXECUTOR_CODEX, EXECUTOR_CLAUDE)

DEFAULT_EXECUTOR_ENV = "ITERIS_EXECUTOR"


def resolve_executor(value: str | None = None, *, env: dict[str, str] | None = None) -> str:
    """Resolve the executor name: explicit value > $ITERIS_EXECUTOR > codex."""
    source_env = os.environ if env is None else env
    name = (value or source_env.get(DEFAULT_EXECUTOR_ENV) or EXECUTOR_CODEX).strip().lower()
    if name not in KNOWN_EXECUTORS:
        raise ValueError(f"unknown executor {name!r}; expected one of {', '.join(KNOWN_EXECUTORS)}")
    return name


def build_claude_command(
    root: Path,
    prompt: str | None = None,
    *,
    executable: str = "claude",
    yolo: bool = True,
    model: str | None = None,
    prompt_argument: bool = True,
) -> list[str]:
    """Interactive Claude Code launch command, mirroring build_codex_command.

    Claude Code has no ``--cd`` flag; the working directory comes from the
    shell command / subprocess cwd, which all launch paths already set.
    ``yolo`` maps to ``--dangerously-skip-permissions`` (approval bypass);
    the matching ``IS_SANDBOX=1`` env lives in main_agent_home_env so the
    bypass-permissions confirmation dialog is skipped under automation.
    """
    del root  # accepted for signature parity with build_codex_command
    cmd = [executable]
    if yolo:
        cmd.append("--dangerously-skip-permissions")
    if model:
        cmd.extend(["--model", model])
    if prompt is not None and prompt_argument:
        cmd.append(prompt)
    return cmd


DEFAULT_CODEX_MODEL = "gpt-5.5"


def build_codex_headless_command(
    *,
    project_root: Path,
    executable: str = "codex",
    model: str,
    reasoning_effort: str,
) -> list[str]:
    """One-shot, non-interactive Codex command for sub-agents/verifiers/judges.

    ``codex exec --json -`` reads the prompt from stdin and streams JSONL events
    on stdout, which the runner captures into ``codex.events.jsonl``. This is the
    single canonical builder; ``agents.runtime`` and ``verification.agent`` used
    to each carry their own copy.
    """
    return [
        executable,
        "exec",
        "--json",
        "-C",
        str(project_root),
        "-m",
        model,
        "--config",
        f"model_reasoning_effort={reasoning_effort}",
        "--dangerously-bypass-approvals-and-sandbox",
        "-",
    ]


def build_claude_headless_command(
    *,
    project_root: Path,
    executable: str = "claude",
    model: str | None = None,
) -> list[str]:
    """One-shot, non-interactive Claude Code command, mirroring the codex builder.

    ``claude -p`` prints the result without entering the interactive UI;
    ``--output-format stream-json`` (which requires ``--verbose``) emits one JSON
    event per line, the same shape the runner captures for codex. The prompt is
    piped on stdin (no prompt argument), matching ``codex exec -``. Claude Code
    has no ``--cd``; the working directory comes from the runner's subprocess
    cwd. ``--dangerously-skip-permissions`` is the approval bypass; the matching
    ``IS_SANDBOX=1`` lives in ``headless_home_env`` so the confirmation dialog is
    skipped under automation.
    """
    del project_root  # accepted for signature parity; cwd is set by the runner
    cmd = [
        executable,
        "-p",
        "--output-format",
        "stream-json",
        "--verbose",
        "--dangerously-skip-permissions",
    ]
    if model:
        cmd.extend(["--model", model])
    return cmd


def headless_home_env(executor: str, *, env: dict[str, str] | None = None) -> dict[str, str]:
    """Env for a headless sub-agent/verifier (NOT the interactive main loop).

    Headless runs capture events from stdout, so unlike the main loop they need
    no per-run transcript/rollout home — they inherit the ambient
    CODEX_HOME/CLAUDE_CONFIG_DIR (and thus the user's login). Codex needs nothing
    extra. Claude needs the same non-interactive guards as the main loop:
    IS_SANDBOX=1 (skip the --dangerously-skip-permissions confirmation) and the
    modal/auto-updater suppression that otherwise freezes unattended runs.
    """
    source_env = os.environ if env is None else env
    if executor == EXECUTOR_CLAUDE:
        return {
            "IS_SANDBOX": "1",
            "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": source_env.get(
                "ITERIS_CLAUDE_DISABLE_NONESSENTIAL_TRAFFIC", "1"
            ),
        }
    return {}


def resolve_agent_model(
    executor: str,
    explicit: str | None = None,
    *,
    kind: str = "agent",
    env: dict[str, str] | None = None,
) -> str | None:
    """Resolve the model for a headless run, per executor.

    ``kind`` is ``"agent"`` (sub-agents) or ``"verification"``. Codex keeps its
    historical default of ``gpt-5.5`` (and the ITERIS_*_MODEL / CODEX_MODEL env
    overrides). Claude defaults to None so the claude CLI uses its own configured
    model unless ITERIS_CLAUDE_MODEL (or an explicit value) is given — Iteris does
    not hardcode a claude model id that could drift.
    """
    if explicit:
        return explicit
    source_env = os.environ if env is None else env
    upper = kind.upper()
    if executor == EXECUTOR_CLAUDE:
        return source_env.get(f"ITERIS_CLAUDE_{upper}_MODEL") or source_env.get("ITERIS_CLAUDE_MODEL") or None
    return (
        source_env.get(f"ITERIS_{upper}_MODEL")
        or source_env.get("CODEX_MODEL")
        or DEFAULT_CODEX_MODEL
    )


def main_agent_home_env(executor: str, home_dir: Path) -> dict[str, str]:
    """Env vars that point the executor's per-run state dir into the project.

    Codex writes rollout JSONL under $CODEX_HOME/sessions/; Claude Code
    writes transcripts under $CLAUDE_CONFIG_DIR/projects/. IS_SANDBOX=1
    tells Claude Code it runs inside a sandbox so --dangerously-skip-
    permissions does not stop on its interactive confirmation dialog.
    IS_SANDBOX is internal Claude Code behavior, not a documented flag —
    re-verify it after Claude Code upgrades.
    """
    if executor == EXECUTOR_CLAUDE:
        # The /goal loop is a Stop hook that blocks turn-end until the goal
        # verifies. Claude Code force-ends the turn after
        # CLAUDE_CODE_STOP_HOOK_BLOCK_CAP consecutive blocks (default 9),
        # silently parking a long run mid-work. Raise the cap so a deep
        # working turn is not killed by the safety valve.
        #
        # CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC=1 suppresses the interactive
        # modals that froze unattended runs: the periodic "How is Claude doing
        # this session?" feedback survey and the auto-updater pause (it also
        # disables telemetry/error reporting, and bundles DISABLE_AUTOUPDATER —
        # desired here, since a mid-run auto-update is unsafe for a long
        # autonomous session). These flags are documented but live in Claude
        # Code's own env handling, so re-verify them after Claude Code upgrades
        # (same fragility caveat as IS_SANDBOX). Override via the ITERIS_*
        # escape hatches if a future version renames or repurposes them.
        return {
            "CLAUDE_CONFIG_DIR": str(home_dir),
            "IS_SANDBOX": "1",
            "CLAUDE_CODE_STOP_HOOK_BLOCK_CAP": os.getenv("ITERIS_CLAUDE_STOP_HOOK_BLOCK_CAP", "1000"),
            "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": os.getenv(
                "ITERIS_CLAUDE_DISABLE_NONESSENTIAL_TRAFFIC", "1"
            ),
        }
    return {"CODEX_HOME": str(home_dir)}


def claude_config_dir(env: dict[str, str] | None = None) -> Path:
    source_env = os.environ if env is None else env
    return Path(source_env.get("CLAUDE_CONFIG_DIR") or Path.home() / ".claude").expanduser()


def claude_state_file(env: dict[str, str] | None = None) -> Path:
    """The mutable Claude Code state file (onboarding, project trust, history).

    With CLAUDE_CONFIG_DIR set it lives inside that dir; otherwise it is
    ``~/.claude.json`` next to (not inside) ``~/.claude``.
    """
    source_env = os.environ if env is None else env
    config_dir = source_env.get("CLAUDE_CONFIG_DIR")
    if config_dir:
        return Path(config_dir).expanduser() / ".claude.json"
    return Path.home() / ".claude.json"


# Files symlinked from the real Claude config dir into a per-run
# CLAUDE_CONFIG_DIR so the interactive main loop stays authenticated and
# keeps its onboarding/trust state while transcripts land in the project.
# Same caveat as the Codex per-run home: if Claude rewrites a linked file
# via rename, the symlink is replaced by a diverging copy; prune_goal_runs
# bounds how long such a copy lives. For .credentials.json that divergence
# matters most: an OAuth token refreshed via rename strands the fresh token
# in the per-run dir while the real config dir keeps the expired one, so a
# later run may need a manual re-login.
_CLAUDE_HOME_LINK_FILES = (".credentials.json", "settings.json")


def _write_json_atomic(path: Path, payload: dict[str, object]) -> None:
    """Atomically replace ``path`` (following symlinks) with ``payload``.

    The per-run Claude home links .claude.json to the user's real state
    file, so resolve first: a plain os.replace on the link path would swap
    the symlink itself for a private copy instead of updating the target.
    """
    target = path.resolve() if path.exists() else path
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=target.parent, prefix=target.name + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, indent=2, ensure_ascii=False) + "\n")
        os.replace(tmp_name, target)
    except BaseException:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def prepare_claude_home(home_dir: Path, *, env: dict[str, str] | None = None) -> Path:
    """Create a per-run CLAUDE_CONFIG_DIR seeded from the user's real one."""
    home_dir.mkdir(parents=True, exist_ok=True)
    source_root = claude_config_dir(env)
    links: list[tuple[Path, Path]] = [
        (source_root / name, home_dir / name) for name in _CLAUDE_HOME_LINK_FILES
    ]
    links.append((claude_state_file(env), home_dir / ".claude.json"))
    for source, link in links:
        if link.exists() or link.is_symlink():
            continue
        if not source.exists():
            continue
        try:
            link.symlink_to(source)
        except OSError:
            pass
    return home_dir


def ensure_claude_project_trusted(project_root: Path, *, env: dict[str, str] | None = None) -> dict[str, object]:
    """Persist Claude Code's folder-trust decision for automated launches.

    Claude Code records per-project trust in the state file's ``projects``
    map (``hasTrustDialogAccepted``). Pre-seeding it keeps the interactive
    launch from stopping on the trust dialog. Best-effort: Claude rewrites
    this file frequently, so a concurrent session may drop the edit; the
    bypass-permissions launch path does not strictly depend on it.
    """
    root = str(project_root.resolve())
    state_path = claude_state_file(env)
    result: dict[str, object] = {
        "config_path": str(state_path),
        "project_path": root,
        "already_trusted": False,
        "updated": False,
    }
    state: dict[str, object] = {}
    if state_path.exists():
        # This is the user's real, global Claude state (onboarding, history,
        # every project's settings). An unreadable read usually means a
        # concurrent Claude session is mid-rewrite — never rebuild the file
        # from scratch in that case, or all of that state is lost.
        try:
            loaded = json.loads(state_path.read_text(encoding="utf-8", errors="replace"))
        except (json.JSONDecodeError, OSError) as exc:
            result["error"] = f"state file unreadable; left untouched: {exc}"
            return result
        if not isinstance(loaded, dict):
            result["error"] = "state file is not a JSON object; left untouched"
            return result
        state = loaded
    projects = state.get("projects")
    if not isinstance(projects, dict):
        projects = {}
        state["projects"] = projects
    entry = projects.get(root)
    if not isinstance(entry, dict):
        entry = {}
        projects[root] = entry
    if entry.get("hasTrustDialogAccepted") is True:
        result["already_trusted"] = True
        return result
    entry["hasTrustDialogAccepted"] = True
    _write_json_atomic(state_path, state)
    result["updated"] = True
    return result

"""Run commands for Iteris public workflow and internal bootstrap."""

from __future__ import annotations

import json
import os
import shlex
import shutil
import subprocess
from pathlib import Path

import typer

from iteris import log
from iteris.bootstrap import run_once
from iteris.commands.common import require_public_project
from iteris.commands.goal import (
    attach_tmux_session,
    build_codex_command,
    build_goal_file_reference_prompt,
    build_goal_log_paths,
    build_goal_prompt,
    build_pipe_pane_command,
    build_project_context_lines,
    build_shell_command,
    build_tmux_shell_command,
    accept_codex_trust_prompt,
    ensure_codex_project_trusted,
    goal_codex_home_dir,
    prepare_codex_home,
    prune_goal_runs,
    resolve_goal_defaults,
    tmux_session_exists,
)
from iteris.codex_logs import build_child_env
from iteris.events import record_event
from iteris.gitops import ensure_gitignore
from iteris.executors import (
    EXECUTOR_CODEX,
    build_claude_command,
    ensure_claude_project_trusted,
    main_agent_home_env,
    prepare_claude_home,
    resolve_executor,
)
from iteris.deploy import skew_warning
from iteris.generalize import generalization_prompt_context
from iteris.project import now_iso, now_stamp, read_json, require_project, session_slug, slugify, source_file, write_json


def default_goal(root: Path) -> str:
    source = source_file(root)
    source_text = f" from `{source.relative_to(root)}`" if source else ""
    return (
        f"Solve the source problem{source_text} end-to-end. Use Iteris memory, TASK_POOL.json, "
        "project references, background subagents when useful, and real verification. "
        "Produce the verified target artifact only after fact, assembly, and goal-success verification pass."
    )


def bootstrap(project_path: str = typer.Argument(".", help="Iteris project path.")) -> None:
    """Run deterministic source bootstrap intake once."""
    log.header("iteris tool bootstrap")
    root = require_project(project_path)
    result = run_once(root)
    log.key_value({key: str(value) for key, value in result.items()})
    log.success("Bootstrap intake completed")


def run(
    project_path: str = typer.Argument(".", help="Iteris project path. Defaults to the current directory."),
    goal: str | None = typer.Option(None, "--goal", "-g", help="Goal text. Defaults to solving the source problem end-to-end."),
    target_artifact: str | None = typer.Option(None, "--target-artifact", "-o", help="Terminal artifact path relative to the project."),
    problem_id: str | None = typer.Option(None, "--problem-id", help="Stable problem id. Defaults to the project directory name."),
    session: str | None = typer.Option(None, "--session", "-s", help="tmux session name. Defaults to iteris-<project>."),
    attach: bool = typer.Option(False, "--attach", help="Attach to tmux after starting. Detach with Ctrl-b then d."),
    print_only: bool = typer.Option(False, "--print", help="Only write the prompt file and print the launch command."),
    foreground: bool = typer.Option(False, "--foreground", help="Run Codex in the current terminal instead of tmux."),
    new_session: bool = typer.Option(False, "--new-session", help="Allow starting another session when the default session already exists."),
    executor: str | None = typer.Option(None, "--executor", "-e", help="Agent CLI for the /goal loop: codex or claude. Sub-agents and verifiers inherit it (override per-command, or verifiers via $ITERIS_VERIFICATION_EXECUTOR). Defaults to $ITERIS_EXECUTOR, then codex."),
    model: str | None = typer.Option(None, "--model", "-m", help="Model passed to the executor CLI (codex -m / claude --model). Defaults to the executor's own default."),
    yolo: bool = typer.Option(True, "--yolo/--no-yolo", help="Bypass executor approvals (codex --yolo / claude --dangerously-skip-permissions)."),
    no_alt_screen: bool = typer.Option(True, "--no-alt-screen/--alt-screen", help="Keep terminal scrollback visible."),
    auto_trust: bool = typer.Option(True, "--auto-trust/--no-auto-trust", help="Accept Codex's project directory trust prompt during automated tmux launch."),
    json_output: bool = typer.Option(False, "--json", help="Print machine-readable JSON."),
) -> None:
    """Start a real Iteris /goal work loop."""
    root = require_public_project(project_path)
    # Deploy-skew preflight: a run launched on stale code silently executes pre-fix
    # behavior. Warn (do not block) when the deployed venv is behind the source.
    _skew = skew_warning()
    if _skew:
        log.warn(f"deploy skew: {_skew}")
    problem_id, target_artifact = resolve_goal_defaults(root, problem_id=problem_id, target_artifact=target_artifact)
    default_session = f"iteris-{session_slug(root.name)}"
    session_name = session or default_session
    if session is None and new_session:
        session_name = f"{session_name}-{slugify(now_stamp(), 32)}"

    try:
        active_session = tmux_session_exists(session_name)
    except RuntimeError:
        active_session = False
    if not print_only and not foreground and active_session:
        payload = _active_session_payload(root, session_name, target_artifact)
        if json_output:
            typer.echo(json.dumps(payload, indent=2, ensure_ascii=False))
            raise typer.Exit(1)
        log.warn(f"Run session already exists: {session_name}")
        _print_existing_session(payload)
        raise typer.Exit(1)

    try:
        executor_name = resolve_executor(executor)
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from None
    binary = shutil.which(executor_name)
    # A generalization child's prompt must keep its lineage block (inherited-fact
    # re-verification discipline, direction context, family/message guidance) —
    # rebuild it from .iteris/generalize.json instead of dropping it.
    gen_context = generalization_prompt_context(root)
    prompt_text = build_goal_prompt(
        goal or (gen_context or {}).get("goal") or default_goal(root),
        target_artifact=target_artifact,
        problem_id=problem_id,
        generalization=gen_context,
        project_context_lines=build_project_context_lines(root),
    )
    prompt_path = root / ".iteris" / "goal_prompt.txt"
    prompt_path.parent.mkdir(parents=True, exist_ok=True)
    prompt_path.write_text(prompt_text + "\n", encoding="utf-8")
    launch_prompt = build_goal_file_reference_prompt(str(prompt_path.relative_to(root)))

    if binary is None and not print_only:
        payload = {
            "project_path": str(root),
            "session_name": session_name,
            "default_session_name": default_session,
            "target_artifact": target_artifact,
            "prompt_file": str(prompt_path.relative_to(root)),
            "error": f"{executor_name} executable not found",
        }
        if json_output:
            typer.echo(json.dumps(payload, indent=2, ensure_ascii=False))
            raise typer.Exit(1)
        log.warn(f"{executor_name} executable not found. Install it or use Iteris tools manually.")
        _print_run_next_steps(payload, started=False)
        raise typer.Exit(1)

    # One stamp shared by the pane/meta logs and the per-run executor home
    # (CODEX_HOME / CLAUDE_CONFIG_DIR) so their directory names correlate
    # (goal-<session>-<stamp>). Both executors share the codex_home/ path so
    # prune_goal_runs and the default gitignore cover them uniformly.
    run_stamp = now_stamp()
    agent_home_dir = goal_codex_home_dir(root, session_name, run_stamp)
    home_env = main_agent_home_env(executor_name, agent_home_dir)
    # The /goal loop is launched through tmux `exec env ... codex`, which bypasses
    # build_child_env() — the helper that pins the iteris console-scripts dir onto
    # every SUB-agent's PATH so `iteris tool ...` resolves in non-login shells.
    # Without the same pin here, the main loop's non-interactive shells lack that
    # dir and bare `iteris tool ...` calls fail with "iteris: command not found",
    # silently disabling the loop's own CLI machinery. Reuse the one mechanism so
    # the guarantee is identical for the loop and its sub-agents, independent of
    # how/where iteris was installed.
    loop_path = build_child_env({}).get("PATH")
    if loop_path:
        home_env["PATH"] = loop_path
    # Export the executor into the loop's subtree so the sub-agents, verifiers,
    # and judgment backends it spawns via `iteris tool ...` default to the SAME
    # backend as the main loop (an explicit --executor / ITERIS_VERIFICATION_EXECUTOR
    # still overrides). This is what makes executor selection framework-wide.
    home_env["ITERIS_EXECUTOR"] = executor_name
    # Re-bake an independently-chosen verification executor into the subtree too.
    # This loop launches through tmux, whose new session inherits the tmux SERVER
    # env (not this process's) — the FI-0036 gotcha. Without re-baking it here,
    # ITERIS_VERIFICATION_EXECUTOR is dropped at the tmux hop and verifiers fall
    # back to the main executor, silently losing cross-model verification.
    verification_executor = os.environ.get("ITERIS_VERIFICATION_EXECUTOR")
    if verification_executor:
        home_env["ITERIS_VERIFICATION_EXECUTOR"] = verification_executor

    if executor_name == EXECUTOR_CODEX:
        agent_cmd = build_codex_command(root, launch_prompt, executable=binary or "codex", yolo=yolo, no_alt_screen=no_alt_screen, model=model)
    else:
        agent_cmd = build_claude_command(root, launch_prompt, executable=binary or "claude", yolo=yolo, model=model)
    shell_cmd = build_shell_command(root, agent_cmd, env_updates=home_env)

    if print_only:
        payload = {
            "project_path": str(root),
            "session_name": session_name,
            "target_artifact": target_artifact,
            "prompt_file": str(prompt_path.relative_to(root)),
            "mode": "print",
            "command": shell_cmd if foreground else shlex.join(["tmux", "new-session", "-d", "-s", session_name, shell_cmd]),
        }
        if json_output:
            typer.echo(json.dumps(payload, indent=2, ensure_ascii=False))
            return
        log.info(f"Prompt file: {payload['prompt_file']}")
        log.info(payload["command"])
        return

    trust_config: dict[str, object] = {"enabled": False}
    if auto_trust:
        try:
            if executor_name == EXECUTOR_CODEX:
                trust_config = {"enabled": True, **ensure_codex_project_trusted(root)}
            else:
                trust_config = {"enabled": True, **ensure_claude_project_trusted(root)}
        except OSError as exc:
            trust_config = {"enabled": True, "error": str(exc)}
            log.warn(f"Could not persist {executor_name} project trust; will fall back to the interactive trust prompt: {exc}")

    def _prepare_agent_home() -> None:
        if executor_name == EXECUTOR_CODEX:
            prepare_codex_home(root, session_name, run_stamp)
            return
        # prepare_codex_home re-asserts the gitignore for projects created
        # before .iteris/codex_home/ entered the defaults; mirror that here.
        try:
            ensure_gitignore(root)
        except OSError:
            pass
        prepare_claude_home(agent_home_dir)

    if foreground:
        prune_goal_runs(root)
        _prepare_agent_home()
        record_event(root, "run_started", {"mode": "foreground", "executor": executor_name, "target_artifact": target_artifact, "prompt_file": str(prompt_path.relative_to(root))})
        _print_run_next_steps(
            {
                "project_path": str(root),
                "session_name": None,
                "default_session_name": default_session,
                "target_artifact": target_artifact,
                "prompt_file": str(prompt_path.relative_to(root)),
                "mode": "foreground",
            },
            started=True,
        )
        env = {**os.environ, **home_env}
        raise typer.Exit(subprocess.run(agent_cmd, cwd=root, env=env, check=False).returncode)

    if shutil.which("tmux") is None:
        raise typer.BadParameter("tmux is not installed. Use `iteris run --foreground` or install tmux.")

    prune_goal_runs(root)
    log_paths = build_goal_log_paths(root, session_name, run_stamp)
    for path in log_paths.values():
        path.parent.mkdir(parents=True, exist_ok=True)
    _prepare_agent_home()
    launch_tmux_cmd = build_tmux_shell_command(session_name)
    pipe_cmd = build_pipe_pane_command(session_name, log_paths["pane_log"])
    respawn_cmd = ["tmux", "respawn-pane", "-t", session_name, "-k", shell_cmd]
    meta = {
        "schema_version": "iteris.goal_launch.v0",
        "created_at": now_iso(),
        "project_path": str(root),
        "session_name": session_name,
        "default_session_name": default_session,
        "problem_id": problem_id,
        "target_artifact": target_artifact,
        "goal": goal or default_goal(root),
        "prompt_file": ".iteris/goal_prompt.txt",
        "pane_log": str(log_paths["pane_log"].relative_to(root)),
        "executor": executor_name,
        "model": model,
        "agent_home_env": home_env,
        # Legacy key names predate multi-executor support; they describe the
        # active executor's home/command regardless of which one launched.
        "codex_home": str(agent_home_dir.relative_to(root)),
        "codex_command": agent_cmd,
        "codex_trust_config": trust_config,
        "tmux_command": launch_tmux_cmd,
        "pipe_command": pipe_cmd,
        "respawn_command": respawn_cmd,
        "prompt_delivery": "prompt_file_reference_argument",
        "public_command": "iteris run",
    }
    log_paths["meta"].write_text(json.dumps(meta, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    subprocess.run(launch_tmux_cmd, check=True)
    subprocess.run(pipe_cmd, check=True)
    subprocess.run(respawn_cmd, check=True)
    _write_current_run(root, session_name=session_name, target_artifact=target_artifact, meta_log=str(log_paths["meta"].relative_to(root)), pane_log=meta["pane_log"])
    if executor_name == EXECUTOR_CODEX:
        # Claude launches with IS_SANDBOX=1 + --dangerously-skip-permissions
        # and pre-seeded folder trust, so only Codex needs the pane watcher.
        trust = accept_codex_trust_prompt(session_name, auto_accept_trust=auto_trust)
        meta["codex_trust_prompt_observed"] = bool(trust["observed"])
        meta["codex_trust_accepted"] = bool(trust["accepted"])
        log_paths["meta"].write_text(json.dumps(meta, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    record_event(root, "run_started", {"mode": "tmux", "session_name": session_name, "executor": executor_name, "target_artifact": target_artifact, "pane_log": meta["pane_log"]})
    payload = {
        "project_path": str(root),
        "session_name": session_name,
        "default_session_name": default_session,
        "target_artifact": target_artifact,
        "prompt_file": ".iteris/goal_prompt.txt",
        "pane_log": meta["pane_log"],
        "meta_log": str(log_paths["meta"].relative_to(root)),
        "mode": "tmux",
        "executor": executor_name,
        "attached": attach,
    }
    if json_output:
        typer.echo(json.dumps(payload, indent=2, ensure_ascii=False))
    else:
        _print_run_next_steps(payload, started=True)
    if attach:
        try:
            attach_tmux_session(session_name)
        except RuntimeError as exc:
            log.warn(str(exc))
            raise typer.Exit(1) from None


def _active_session_payload(root: Path, session_name: str, target_artifact: str) -> dict[str, object]:
    default_session = f"iteris-{session_slug(root.name)}"
    return {
        "project_path": str(root),
        "session_name": session_name,
        "default_session_name": default_session,
        "target_artifact": target_artifact,
        "active": True,
        "next_commands": _session_commands(session_name, default_session=default_session),
    }


def _print_existing_session(payload: dict[str, object]) -> None:
    log.panel(
        "\n".join(
            [
                f"Session: {payload['session_name']}",
                f"Target: {payload['target_artifact']}",
                "",
                "Monitor progress:",
                "  iteris monitor",
                "",
                "Attach to terminal:",
                f"  {_session_command('iteris attach', str(payload['session_name']), default_session=str(payload['default_session_name']))}",
                "",
                "Stop it:",
                f"  {_session_command('iteris stop', str(payload['session_name']), default_session=str(payload['default_session_name']))}",
                "",
                "Start another session intentionally:",
                "  iteris run --new-session",
            ]
        ),
        title="Run already active",
    )


def _print_run_next_steps(payload: dict[str, object], *, started: bool) -> None:
    status = "started" if started else "not started"
    session = payload.get("session_name")
    default_session = str(payload.get("default_session_name") or session or "")
    lines = [
        f"Project: {payload['project_path']}",
        f"Status: {status}",
        f"Target: {payload['target_artifact']}",
        f"Prompt: {payload['prompt_file']}",
    ]
    if session:
        lines.extend(
            [
                f"Session: {session}",
                f"Pane log: {payload.get('pane_log', '(not started)')}",
                "",
                "Monitor progress without knowing tmux:",
                "  iteris monitor",
                "  iteris dashboard",
                "",
                "Attach to the live terminal:",
                f"  {_session_command('iteris attach', str(session), default_session=default_session)}",
                "  detach with Ctrl-b then d",
                "",
                "Stop the run:",
                f"  {_session_command('iteris stop', str(session), default_session=default_session)}",
                "",
                "Prepare review materials:",
                f"  {_session_command('iteris review', str(session), default_session=default_session)}",
            ]
        )
    log.panel("\n".join(lines), title="Iteris run")


def _session_command(command: str, session_name: str, *, default_session: str) -> str:
    if session_name == default_session:
        return command
    return f"{command} --session {session_name}"


def _session_commands(session_name: str, *, default_session: str) -> list[str]:
    suffix = "" if session_name == default_session else f" --session {session_name}"
    return [f"iteris status{suffix}", "iteris monitor", "iteris dashboard", f"iteris attach{suffix}", f"iteris stop{suffix}", "iteris run --new-session"]


def _write_current_run(root: Path, *, session_name: str, target_artifact: str, meta_log: str, pane_log: str) -> None:
    state_path = root / ".iteris" / "current_run.json"
    previous = read_json(state_path, default={})
    history = previous.get("history", []) if isinstance(previous, dict) else []
    write_json(
        state_path,
        {
            "schema_version": "iteris.current_run.v0",
            "updated_at": now_iso(),
            "session_name": session_name,
            "target_artifact": target_artifact,
            "meta_log": meta_log,
            "pane_log": pane_log,
            "history": [*history[-9:], {"session_name": session_name, "target_artifact": target_artifact, "meta_log": meta_log, "pane_log": pane_log}],
        },
    )

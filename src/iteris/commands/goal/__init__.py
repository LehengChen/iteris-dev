"""Helpers for Codex /goal-oriented sessions."""

from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import signal
import subprocess
import time
from pathlib import Path, PurePosixPath
from typing import Callable, Sequence

import typer

from iteris import log
from iteris.agents.runtime import drain_project_workers
from iteris.tmux import (  # noqa: F401  (re-exported for existing import sites)
    attach_tmux_session,
    build_interrupt_command,
    build_kill_session_command,
    build_pipe_pane_command,
    capture_pane,
    stop_tmux_session,
    tmux_attach_command,
    tmux_session_exists,
)
from iteris.gitops import GitError, checkpoint as git_checkpoint, ensure_gitignore, status as git_status
from iteris.project import now_iso, now_stamp, read_json, require_project, session_slug, slugify, write_json
from iteris.commands.goal.targets import (  # noqa: F401  (re-exported for existing import sites)
    default_problem_id,
    default_target_artifact,
    project_target_artifact,
    reduced_artifact_for,
    resolve_goal_defaults,
    verified_artifact_for,
)
from iteris.commands.goal.processes import (  # noqa: F401  (re-exported for existing import sites)
    stop_verification_agents,
    verifier_processes,
    _matching_verifier_processes,
    _process_env_by_pid,
)
from iteris.commands.goal.prompt import (  # noqa: F401  (re-exported for existing import sites)
    build_generalization_block,
    build_goal_file_reference_prompt,
    build_goal_prompt,
    build_project_context_lines,
)
from iteris.commands.goal.session import (  # noqa: F401  (re-exported for existing import sites)
    accept_codex_trust_prompt,
    build_codex_command,
    build_load_prompt_buffer_command,
    build_paste_prompt_buffer_command,
    build_send_keys_command,
    build_shell_command,
    build_submit_prompt_command,
    build_tmux_command,
    build_tmux_shell_command,
    codex_home,
    codex_project_trust_section,
    codex_prompt_ready,
    codex_trust_prompt_present,
    ensure_codex_project_trusted,
    find_run_rollout,
    goal_codex_home_dir,
    prepare_codex_home,
    prepare_codex_prompt,
    wait_for_codex_prompt,
)
from iteris.commands.goal.logs import (  # noqa: F401  (re-exported for existing import sites)
    _live_goal_session_slugs,
    _mtime_or_zero,
    build_goal_log_paths,
    latest_goal_logs,
    prune_goal_runs,
)
from iteris.commands.goal.finalize import (  # noqa: F401  (re-exported for existing import sites)
    _emit_verification_status,
    _latest_passed_verification,
    _stamp_status_last_updated,
    _stamp_status_phase,
    _verification_mentions_target,
    build_goal_finalize_report,
)

app = typer.Typer(help="Helpers for launching Codex goal sessions.")


# Files symlinked from the real ~/.codex into a per-run CODEX_HOME so the
# interactive main loop stays authenticated/configured while its structured
# rollout JSONL is redirected into the project (see prepare_codex_home).


@app.command("finalize")
def finalize(
    project_path: str = typer.Argument(".", help="Iteris project path."),
    target_artifact: str = typer.Option(..., "--target-artifact", "-o", help="Terminal artifact path relative to the project."),
    require_clean: bool = typer.Option(True, "--require-clean/--no-require-clean", help="Require a clean git worktree before reporting ok."),
    principled_stop: bool = typer.Option(False, "--principled-stop/--no-principled-stop", help="Finalize via a certified principled stop — gate on a passed `principled_stop` verification instead of `goal_success`, and emit answer_reduced_verified.md. Use only when the full goal is certified unreachable-as-stated / reduced to an open subproblem."),
    json_output: bool = typer.Option(False, "--json", help="Print machine-readable JSON."),
) -> None:
    """Check whether the project satisfies the final goal-completion gate."""
    root = require_project(project_path)
    terminal_mode = "principled_stop" if principled_stop else "goal_success"
    report = build_goal_finalize_report(root, target_artifact=target_artifact, require_clean=require_clean, terminal_mode=terminal_mode)
    verified_artifact = _emit_verification_status(root, target_artifact=target_artifact, report=report)
    if verified_artifact:
        report["verified_artifact"] = verified_artifact
    if report["ok"] and _stamp_status_phase(root, "principled_stop_certified" if principled_stop else "goal_success_verified"):
        # Keep finalize idempotent under --require-clean: commit the stamp.
        try:
            git_checkpoint(root, message="checkpoint: finalize phase stamp")
        except GitError:
            pass
    if json_output:
        typer.echo(json.dumps(report, indent=2, ensure_ascii=False))
        if not report["ok"]:
            raise typer.Exit(1)
        return
    if report["ok"]:
        log.success("Goal finalization checks passed")
    else:
        log.warn("Goal finalization checks failed")
    rows = [(str(check["name"]), "ok" if check["ok"] else "failed", str(check.get("detail") or "")) for check in report["checks"]]
    log.results_table(rows, title="Goal finalization")
    if not report["ok"]:
        raise typer.Exit(1)


@app.command("tmux")
def tmux_command(
    project_path: str = typer.Argument(".", help="Iteris project path."),
    goal: str = typer.Option("Solve the project goal end-to-end using Iteris memory, tasks, and real verification.", "--goal", "-g"),
    target_artifact: str | None = typer.Option(None, "--target-artifact", "-o", help="Terminal artifact path relative to the project. Defaults to results/<problem-id>/answer.md (finalize emits the verified copy answer_verified.md on goal-success pass)."),
    problem_id: str | None = typer.Option(None, "--problem-id", help="Stable problem id for result paths and memory. Defaults to the project directory name."),
    session: str | None = typer.Option(None, "--session", "-s"),
    yolo: bool = typer.Option(True, "--yolo/--no-yolo", help="Use codex --yolo when launching."),
    no_alt_screen: bool = typer.Option(True, "--no-alt-screen/--alt-screen", help="Keep terminal scrollback visible."),
    detached: bool = typer.Option(False, "--detach/--attach", help="Launch tmux detached when --launch is used."),
    print_only: bool = typer.Option(True, "--print-only/--launch", help="Print command instead of launching."),
    allow_blocker_completion: bool = typer.Option(False, "--allow-blocker-completion/--no-blocker-completion", help="Allow a verified blocker or gap report to satisfy the terminal goal."),
    auto_trust: bool = typer.Option(True, "--auto-trust/--no-auto-trust", help="Accept Codex's project directory trust prompt during automated tmux launch."),
) -> None:
    """Print or launch a tmux command for a Codex goal session."""
    root = require_project(project_path)
    problem_id, target_artifact = resolve_goal_defaults(root, problem_id=problem_id, target_artifact=target_artifact)
    session_name = session or f"iteris-{session_slug(root.name)}"
    codex = shutil.which("codex")
    prompt = build_goal_prompt(
        goal,
        target_artifact=target_artifact,
        problem_id=problem_id,
        allow_blocker_completion=allow_blocker_completion,
        project_context_lines=build_project_context_lines(root),
    )
    prompt_path = root / ".iteris" / "goal_prompt.txt"
    prompt_path.parent.mkdir(parents=True, exist_ok=True)
    prompt_path.write_text(prompt + "\n", encoding="utf-8")
    launch_prompt = build_goal_file_reference_prompt(str(prompt_path.relative_to(root)))
    if print_only or codex is None:
        codex_cmd = build_codex_command(root, launch_prompt, executable=codex or "codex", yolo=yolo, no_alt_screen=no_alt_screen)
        shell_cmd = build_shell_command(root, codex_cmd)
        tmux_cmd = build_tmux_command(session_name, shell_cmd, detached=detached)
        if codex is None:
            log.warn("codex executable is not installed; printing launch command only")
        log.info(f"Prompt file: {prompt_path.relative_to(root)}")
        log.info(" ".join(shlex.quote(part) for part in tmux_cmd))
        return
    trust_config: dict[str, object] = {"enabled": False}
    if auto_trust:
        try:
            trust_config = {"enabled": True, **ensure_codex_project_trusted(root)}
        except OSError as exc:
            trust_config = {"enabled": True, "error": str(exc)}
            log.warn(f"Could not persist Codex project trust; will fall back to the interactive trust prompt: {exc}")
    log_paths = build_goal_log_paths(root, session_name)
    for path in log_paths.values():
        path.parent.mkdir(parents=True, exist_ok=True)
    codex_cmd = build_codex_command(root, launch_prompt, executable=codex, yolo=yolo, no_alt_screen=no_alt_screen)
    shell_cmd = build_shell_command(root, codex_cmd)
    tmux_cmd = build_tmux_command(session_name, shell_cmd, detached=detached)
    launch_tmux_cmd = build_tmux_shell_command(session_name)
    pipe_cmd = build_pipe_pane_command(session_name, log_paths["pane_log"])
    send_cmd = build_send_keys_command(session_name, shell_cmd)
    meta = {
        "schema_version": "iteris.goal_launch.v0",
        "created_at": now_iso(),
        "project_path": str(root),
        "session_name": session_name,
        "goal": goal,
        "problem_id": problem_id,
        "target_artifact": target_artifact,
        "verification_gate": "goal_success",
        "allow_blocker_completion": allow_blocker_completion,
        "auto_trust": auto_trust,
        "detached": detached,
        "prompt_file": ".iteris/goal_prompt.txt",
        "launch_prompt": launch_prompt,
        "pane_log": str(log_paths["pane_log"].relative_to(root)),
        "codex_command": codex_cmd,
        "codex_trust_config": trust_config,
        "tmux_print_command": tmux_cmd,
        "tmux_command": launch_tmux_cmd,
        "pipe_command": pipe_cmd,
        "send_command": send_cmd,
        "prompt_delivery": "prompt_file_reference_argument",
    }
    log_paths["meta"].write_text(json.dumps(meta, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    subprocess.run(launch_tmux_cmd, check=True)
    subprocess.run(pipe_cmd, check=True)
    subprocess.run(send_cmd, check=True)
    trust = accept_codex_trust_prompt(session_name, auto_accept_trust=auto_trust)
    meta["codex_trust_prompt_observed"] = bool(trust["observed"])
    meta["codex_trust_accepted"] = bool(trust["accepted"])
    log_paths["meta"].write_text(json.dumps(meta, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    log.success(f"Goal session launched: {session_name}")
    log.info(f"Pane log: {log_paths['pane_log']}")
    if not detached:
        try:
            attach_tmux_session(session_name)
        except RuntimeError as exc:
            log.warn(str(exc))
            raise typer.Exit(1) from None


@app.command("inspect")
def inspect(
    project_path: str = typer.Argument(".", help="Iteris project path."),
    session: str | None = typer.Option(None, "--session", "-s", help="tmux session name."),
    lines: int = typer.Option(200, "--lines", "-n", help="Pane lines to capture."),
    json_output: bool = typer.Option(False, "--json", help="Print machine-readable JSON."),
) -> None:
    """Show latest goal logs and optionally print a live tmux pane view."""
    root = require_project(project_path)
    session_name = session or f"iteris-{session_slug(root.name)}"
    try:
        pane_text = capture_pane(session_name, lines=lines)
    except RuntimeError as exc:
        logs = latest_goal_logs(root, session_name)
        payload = {"session_name": session_name, "active": False, "error": str(exc), "logs": logs}
        if json_output:
            typer.echo(json.dumps(payload, indent=2, ensure_ascii=False))
            return
        log.warn(str(exc))
        log.key_value({"Session": session_name, "Meta": logs.get("meta") or "(none)", "Pane log": logs.get("pane_log") or "(none)"})
        return
    logs = latest_goal_logs(root, session_name)
    payload = {"session_name": session_name, "active": True, "logs": logs, "pane_text": pane_text}
    if json_output:
        typer.echo(json.dumps(payload, indent=2, ensure_ascii=False))
        return
    log.key_value({"Session": session_name, "Meta": logs.get("meta") or "(none)", "Pane log": logs.get("pane_log") or "(none)"})
    typer.echo(pane_text)


@app.command("stop")
def stop(
    project_path: str = typer.Argument(".", help="Iteris project path."),
    session: str | None = typer.Option(None, "--session", "-s", help="tmux session name."),
    force_after: float = typer.Option(5.0, "--force-after", help="Seconds to wait after Ctrl-C before forcing tmux stop."),
    force: bool = typer.Option(True, "--force/--no-force", help="Stop tmux if the session stays active after Ctrl-C."),
    kill_verifiers: bool = typer.Option(True, "--kill-verifiers/--keep-verifiers", help="Also stop orphaned Iteris verification-agent processes for this project."),
    kill_agents: bool = typer.Option(True, "--kill-agents/--keep-agents", help="Also drain this run's detached sub-agent workers and their codex subtrees (otherwise they keep running and burning budget after the loop stops)."),
    json_output: bool = typer.Option(False, "--json", help="Print machine-readable JSON."),
) -> None:
    """Stop a Codex goal tmux session and report its reproducibility logs."""
    root = require_project(project_path)
    session_name = session or f"iteris-{session_slug(root.name)}"
    logs = latest_goal_logs(root, session_name)
    try:
        result = stop_tmux_session(session_name, force_after=force_after, force=force)
    except RuntimeError as exc:
        result = {"stopped": False, "active": False, "reason": str(exc), "actions": []}
    verifier_result = stop_verification_agents(root, force_after=force_after, force=force) if kill_verifiers else None
    agent_result = drain_project_workers(root, force_after=force_after) if kill_agents else None
    payload = {
        "session_name": session_name,
        **result,
        "logs": logs,
        "verification_agents": verifier_result,
        "agent_workers": agent_result,
    }
    if json_output:
        typer.echo(json.dumps(payload, indent=2, ensure_ascii=False))
        return
    if payload["stopped"]:
        log.success(f"Goal session stopped: {session_name}")
    elif payload["active"]:
        log.warn(f"Goal session still active: {session_name}")
    else:
        log.warn(f"Goal session not active: {session_name}")
    log.key_value(
        {
            "Session": session_name,
            "Reason": str(payload["reason"]),
            "Meta": logs.get("meta") or "(none)",
            "Pane log": logs.get("pane_log") or "(none)",
            "Verification agents stopped": str(verifier_result.get("stopped")) if verifier_result else "not requested",
            "Sub-agent workers drained": str(agent_result.get("count")) if agent_result else "not requested",
        }
    )

"""Operator command group: `iteris tool generalize ...`.

Currently exposes `analyze`, which launches a Codex agent (detached tmux, like
`iteris run`) to read a verified result and emit schema-conforming generalization
directions, plus a `--validate` mode the agent uses to self-check its output.
"""

from __future__ import annotations

import json
import os
import shlex
import shutil
import subprocess
from pathlib import Path

import typer

from iteris import log
from iteris.commands.common import require_public_project
from iteris.commands.goal import (
    accept_codex_trust_prompt,
    build_codex_command,
    build_goal_log_paths,
    build_pipe_pane_command,
    build_shell_command,
    build_tmux_shell_command,
    ensure_codex_project_trusted,
    goal_codex_home_dir,
    prepare_codex_home,
    prune_goal_runs,
    tmux_session_exists,
)
from iteris.codex_logs import build_child_env
from iteris.executors import (
    EXECUTOR_CODEX,
    build_claude_command,
    ensure_claude_project_trusted,
    main_agent_home_env,
    prepare_claude_home,
    resolve_executor,
)
from iteris.gitops import ensure_gitignore
from iteris.generalize import resolve_source_result
from iteris.generalize_analyze import build_analyze_prompt, validate_analysis_file
from iteris.project import now_iso, now_stamp, require_project, session_slug, slugify

app = typer.Typer(help="Generalization tooling (analysis of verified results).")

ANALYSIS_JSON = "generalize/analysis.json"
DIRECTIONS_DIR = "generalize"


def _family_pool_digest(project_root: Path) -> str | None:
    """Existing family direction pool, for dedup + cross-branch synthesis."""
    from iteris.evolve import has_evolve_state, read_state
    from iteris.memory.family import resolve_family_root

    family_root = resolve_family_root(project_root)
    if family_root is None or not has_evolve_state(family_root):
        return None
    state = read_state(family_root)
    lines = []
    goal = str(state.get("goal") or "").strip()
    if goal:
        lines.append(f"Family goal (including its steering priorities): {goal}")
    for entry in state.get("direction_pool", []):
        status = entry.get("status")
        line = (
            f"- [{status}] {entry.get('title') or entry.get('direction_id')} "
            f"(kind={entry.get('kind')}, uses_inputs={entry.get('uses_inputs')})"
        )
        # Rejection reasons are the anti-repetition signal: per-node analyses
        # keep regenerating the same genres without them.
        why = entry.get("superseded_why") or entry.get("revision_why")
        if status == "vetoed":
            line += " — VETOED by the human; this genre is off-limits"
            if entry.get("vetoed_why"):
                line += f": {entry['vetoed_why']}"
        elif status == "superseded" and why:
            line += f" — superseded: {why}"
        lines.append(line)
    for item in state.get("boundary", []):
        lines.append(
            f"- [boundary] {item.get('direction_id')}: {item.get('verdict')} — {item.get('reason_summary')}"
        )
    return "\n".join(lines) if lines else None


def _analyze_launch_prompt(prompt_file: str) -> str:
    return (
        f"Read `{prompt_file}` and execute the full generalization-analysis task it "
        "specifies. The file is authoritative. Keep working until the analysis JSON "
        "validates and every direction markdown file exists."
    )


@app.command("analyze")
def analyze(
    project_path: str = typer.Argument(".", help="Iteris project to analyze for generalization directions."),
    source_result: str | None = typer.Option(
        None, "--source-result", help="Verified result to analyze, relative to the project. Defaults to the project's target_artifact."
    ),
    directions: int = typer.Option(3, "--directions", "-n", help="Number of generalization directions to request."),
    session: str | None = typer.Option(None, "--session", "-s", help="tmux session name. Defaults to iteris-analyze-<project>."),
    validate: str | None = typer.Option(
        None, "--validate", help="Validate an existing analysis JSON file against the schema and exit (used by the agent to self-check)."
    ),
    print_only: bool = typer.Option(False, "--print", help="Only write the prompt file and print the launch command."),
    foreground: bool = typer.Option(False, "--foreground", help="Run the executor in the current terminal instead of tmux."),
    executor: str | None = typer.Option(None, "--executor", "-e", help="Agent CLI: codex or claude. Defaults to $ITERIS_EXECUTOR, then codex."),
    model: str | None = typer.Option(None, "--model", "-m", help="Model passed to the executor CLI. Defaults to the executor's own default."),
    auto_trust: bool = typer.Option(True, "--auto-trust/--no-auto-trust", help="Accept the executor's project trust prompt during launch."),
    json_output: bool = typer.Option(False, "--json", help="Print machine-readable JSON."),
) -> None:
    """Launch an executor agent that maps generalization directions for a verified result."""
    root = require_public_project(project_path)

    # --validate short-circuit: pure local schema check, no Codex.
    if validate is not None:
        target = root / validate if not Path(validate).is_absolute() else Path(validate)
        result = validate_analysis_file(target)
        if json_output:
            typer.echo(json.dumps(result, indent=2, ensure_ascii=False))
        else:
            (log.success if result["ok"] else log.warn)(
                f"analysis {'valid' if result['ok'] else 'invalid'}: {target} "
                f"({result['direction_count']} directions)"
            )
            for err in result["errors"]:
                log.warn(f"  - {err}")
            for warning in result.get("warnings", []):
                log.info(f"  - warning: {warning}")
        raise typer.Exit(0 if result["ok"] else 1)

    try:
        source_result_rel = resolve_source_result(root, source_result)
    except Exception as exc:
        raise typer.BadParameter(str(exc)) from exc

    (root / DIRECTIONS_DIR).mkdir(parents=True, exist_ok=True)
    validate_command = f"iteris tool generalize analyze . --validate {ANALYSIS_JSON} --json"
    prompt_text = build_analyze_prompt(
        parent_name=root.name,
        source_result_rel=source_result_rel,
        n_directions=directions,
        analysis_json_path=ANALYSIS_JSON,
        directions_dir=DIRECTIONS_DIR,
        validate_command=validate_command,
        family_digest=_family_pool_digest(root),
    )
    prompt_path = root / ".iteris" / "generalize_analyze_prompt.txt"
    prompt_path.parent.mkdir(parents=True, exist_ok=True)
    prompt_path.write_text(prompt_text + "\n", encoding="utf-8")
    launch_prompt = _analyze_launch_prompt(str(prompt_path.relative_to(root)))

    session_name = session or f"iteris-analyze-{session_slug(root.name)}"
    try:
        executor_name = resolve_executor(executor)
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from None
    binary = shutil.which(executor_name)
    if executor_name == EXECUTOR_CODEX:
        agent_cmd = build_codex_command(root, launch_prompt, executable=binary or "codex", model=model)
    else:
        agent_cmd = build_claude_command(root, launch_prompt, executable=binary or "claude", model=model)
    # Same per-run executor-home pipeline as `iteris run`: one stamp correlates
    # pane/meta logs with the home dir, so the analysis run's structured rollout
    # /transcript lands in-project instead of leaking to ~/.codex or ~/.claude.
    # Both executors share the codex_home/ path so prune_goal_runs and the
    # default gitignore cover them uniformly.
    run_stamp = now_stamp()
    codex_home_dir = goal_codex_home_dir(root, session_name, run_stamp)
    home_env = main_agent_home_env(executor_name, codex_home_dir)
    # Pin the iteris console-scripts dir onto the launch env so the analysis
    # agent's `iteris tool generalize analyze . --validate` resolves in the
    # non-login shells tmux/exec hands it (same fix as the /goal loop).
    loop_path = build_child_env({}).get("PATH")
    if loop_path:
        home_env["PATH"] = loop_path
    shell_cmd = build_shell_command(root, agent_cmd, env_updates=home_env)

    def _prepare_agent_home() -> None:
        if executor_name == EXECUTOR_CODEX:
            prepare_codex_home(root, session_name, run_stamp)
            return
        try:
            ensure_gitignore(root)
        except OSError:
            pass
        prepare_claude_home(codex_home_dir)

    payload = {
        "schema_version": "iteris.generalize_analyze.v0",
        "project_path": str(root),
        "source_result": source_result_rel,
        "directions_requested": directions,
        "session_name": session_name,
        "prompt_file": str(prompt_path.relative_to(root)),
        "analysis_json": ANALYSIS_JSON,
        "validate_command": validate_command,
    }

    if print_only or binary is None:
        payload["mode"] = "print"
        payload["command"] = shell_cmd if foreground else shlex.join(
            ["tmux", "new-session", "-d", "-s", session_name, shell_cmd]
        )
        if json_output:
            typer.echo(json.dumps(payload, indent=2, ensure_ascii=False))
            return
        if binary is None:
            log.warn(f"{executor_name} executable not found; printing launch command only")
        log.info(f"Prompt file: {payload['prompt_file']}")
        log.info(payload["command"])
        return

    if foreground:
        prune_goal_runs(root)
        _prepare_agent_home()
        payload["mode"] = "foreground"
        if json_output:
            typer.echo(json.dumps(payload, indent=2, ensure_ascii=False))
        env = {**os.environ, **home_env}
        raise typer.Exit(subprocess.run(agent_cmd, cwd=root, env=env, check=False).returncode)

    if shutil.which("tmux") is None:
        raise typer.BadParameter("tmux is not installed. Use --foreground or --print, or install tmux.")
    if tmux_session_exists(session_name):
        raise typer.BadParameter(f"analysis session already exists: {session_name}. Use --session <name> or stop it.")

    if auto_trust:
        try:
            if executor_name == EXECUTOR_CODEX:
                ensure_codex_project_trusted(root)
            else:
                ensure_claude_project_trusted(root)
        except OSError as exc:
            log.warn(f"Could not persist {executor_name} project trust: {exc}")

    prune_goal_runs(root)
    log_paths = build_goal_log_paths(root, session_name, run_stamp)
    for path in log_paths.values():
        path.parent.mkdir(parents=True, exist_ok=True)
    _prepare_agent_home()
    launch_tmux_cmd = build_tmux_shell_command(session_name)
    pipe_cmd = build_pipe_pane_command(session_name, log_paths["pane_log"])
    respawn_cmd = ["tmux", "respawn-pane", "-t", session_name, "-k", shell_cmd]
    meta = {
        "schema_version": "iteris.generalize_analyze_launch.v0",
        "created_at": now_iso(),
        "project_path": str(root),
        "session_name": session_name,
        "source_result": source_result_rel,
        "prompt_file": str(prompt_path.relative_to(root)),
        "pane_log": str(log_paths["pane_log"].relative_to(root)),
        "executor": executor_name,
        "model": model,
        # Legacy key names predate multi-executor support; they describe the
        # active executor's home/command regardless of which one launched.
        "codex_home": str(codex_home_dir.relative_to(root)),
        "codex_command": agent_cmd,
    }
    log_paths["meta"].write_text(json.dumps(meta, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    subprocess.run(launch_tmux_cmd, check=True)
    subprocess.run(pipe_cmd, check=True)
    subprocess.run(respawn_cmd, check=True)
    if executor_name == EXECUTOR_CODEX:
        # Claude launches with IS_SANDBOX=1 + --dangerously-skip-permissions and
        # pre-seeded folder trust, so only Codex needs the pane trust watcher.
        accept_codex_trust_prompt(session_name, auto_accept_trust=auto_trust)

    payload["mode"] = "tmux"
    payload["pane_log"] = meta["pane_log"]
    if json_output:
        typer.echo(json.dumps(payload, indent=2, ensure_ascii=False))
        return
    log.panel(
        "\n".join(
            [
                f"Project: {root}",
                f"Source result: {source_result_rel}",
                f"Directions requested: {directions}",
                f"Session: {session_name}",
                f"Output: {ANALYSIS_JSON} + {DIRECTIONS_DIR}/auto-NN-*.md",
                "",
                "Monitor progress:",
                "  iteris monitor",
                "  iteris dashboard",
                f"  iteris status --session {session_name}",
                "",
                "When done, seed from a direction:",
                f"  iteris generalize . --direction {DIRECTIONS_DIR}/auto-01-<slug>.md",
            ]
        ),
        title="Generalization analysis launched",
    )

"""Interactive iteris monitor command."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

import typer

from iteris import log
from iteris.commands.goal import build_codex_command
from iteris.executors import EXECUTOR_CODEX, build_claude_command, headless_home_env, resolve_executor
from iteris.guide.context import build_monitor_handoff
from iteris.guide.environment import check_environment
from iteris.guide.index import (
    detect_project_role,
    ensure_project_guide_files,
    index_needs_refresh,
    refresh_project_index,
)
from iteris.guide.locale import WIZARD_ACTION, choose_locale, t
from iteris.guide.lookups import default_lookups, lookups_for_message
from iteris.guide.paths import local_handoff_path, project_handoff_path
from iteris.guide.prompt import opening_user_message
from iteris.guide.welcome import resolve_menu_input, welcome_payload, welcome_text
from iteris.guide.wizard import run_new_project_wizard
from iteris.project import is_project, resolve_project


def _run_setup_gate(*, executor: str | None, skip: bool, quiet: bool = False) -> str:
    if skip:
        return resolve_executor(executor)
    env = check_environment()
    if not env.get("has_executor"):
        log.error("No agent CLI found. Install codex or claude, then rerun iteris monitor.")
        for hint in env.get("hints") or []:
            log.info(hint)
        raise typer.Exit(1)
    try:
        requested = executor or os.environ.get("ITERIS_EXECUTOR")
        if requested:
            chosen = resolve_executor(executor)
        elif env.get("codex"):
            chosen = EXECUTOR_CODEX
        elif env.get("claude"):
            chosen = "claude"
        else:
            chosen = resolve_executor(executor)
    except ValueError as exc:
        log.error(str(exc))
        raise typer.Exit(1) from None
    if not env.get(chosen):
        log.error(f"{chosen} executable not found. Install it or choose another executor with --executor.")
        raise typer.Exit(1)
    os.environ["ITERIS_EXECUTOR"] = chosen
    if not quiet:
        log.info(f"Using executor: {chosen} (run `{chosen}` once if you have not logged in yet)")
    if not quiet and not env.get("ready_for_monitor"):
        for hint in env.get("hints") or []:
            log.warn(hint)
    return chosen


def _resolve_project_root(project_path: str) -> Path | None:
    root = resolve_project(project_path)
    if is_project(root):
        return root
    return None


def _handoff_path(*, root: Path | None, cwd: Path) -> Path:
    return project_handoff_path(root) if root is not None else local_handoff_path(cwd)


def _session_command(*, executor: str, cwd: Path, initial_message: str) -> list[str]:
    binary = shutil.which(executor) or executor
    if executor == EXECUTOR_CODEX:
        return build_codex_command(cwd, initial_message, executable=binary, yolo=True, no_alt_screen=True)
    return build_claude_command(cwd, initial_message, executable=binary, yolo=True)


def _session_env_updates(executor: str) -> dict[str, str]:
    return headless_home_env(executor)


def _write_handoff(
    *,
    root: Path | None,
    cwd: Path,
    role: str,
    executor: str,
    locale: str,
    user_text: str,
    lookups: dict[str, Any],
) -> Path:
    handoff = build_monitor_handoff(
        project_root=root,
        user_message=user_text,
        lookups=lookups,
        role=role,
        executor=executor,
        locale=locale,
    )
    path = _handoff_path(root=root, cwd=cwd)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(handoff, encoding="utf-8")
    return path


def _display_path(path: Path, *, cwd: Path) -> str:
    try:
        return str(path.resolve().relative_to(cwd.resolve()))
    except ValueError:
        return str(path.resolve())


def _handoff_launch_prompt(
    handoff_path: Path,
    *,
    cwd: Path,
    user_text: str,
    locale: str = "zh",
    inline_limit: int = 12000,
) -> str:
    handoff = handoff_path.read_text(encoding="utf-8")
    if len(handoff) <= inline_limit:
        return handoff
    display = _display_path(handoff_path, cwd=cwd)
    if locale == "zh":
        return "\n".join(
            [
                "Iteris monitor 已为本次 session 准备完整 handoff 上下文。",
                "",
                f"完整 handoff 文件：`{display}`",
                f"Handoff 大小：{len(handoff)} 字符；为避免命令参数过长，这里不会完整粘贴。",
                "",
                "回答用户问题前，请先读取完整 handoff 文件。未读取前不要回答用户问题。该文件包含 monitor 规则、项目/index 上下文、实时 lookup JSON 和用户请求。",
                "",
                "# USER MESSAGE",
                user_text,
            ]
        )
    return "\n".join(
        [
            "Iteris monitor prepared a full handoff context for this session.",
            "",
            f"Full handoff file: `{display}`",
            f"Handoff size: {len(handoff)} characters; it is intentionally not pasted in full.",
            "",
            "Please read the full handoff file before answering the user. Do not answer the user until you have read it. It contains the monitor rules, project/index context, live lookup JSON, and the user's request.",
            "",
            "# USER MESSAGE",
            user_text,
        ]
    )


def monitor(
    project_path: str = typer.Argument(".", help="Iteris project path, or any directory."),
    executor: str | None = typer.Option(None, "--executor", "-e", help="Agent CLI: codex or claude."),
    lang: str | None = typer.Option(None, "--lang", help="Interface language: zh or en."),
    json_output: bool = typer.Option(False, "--json", help="Print handoff JSON instead of launching a session."),
    message: str | None = typer.Option(
        None,
        "--message",
        "-m",
        help="Initial user message to hand off; combine with --json to avoid opening a session.",
    ),
    no_setup: bool = typer.Option(False, "--no-setup", help="Skip setup gate (tests only)."),
    open_only: bool = typer.Option(
        False,
        "--open-only",
        help="Open an interactive session with the default opening context.",
    ),
    welcome_only: bool = typer.Option(False, "--welcome-only", help="Print static welcome and exit."),
) -> None:
    """Primary human interaction entry point for setup, projects, runs, and evolve."""
    chosen_executor = _run_setup_gate(executor=executor, skip=no_setup, quiet=json_output)
    locale = choose_locale(lang, skip_prompt=welcome_only or json_output or message is not None or no_setup)
    root = _resolve_project_root(project_path)
    if root is not None:
        if index_needs_refresh(root):
            refresh_project_index(root)
        ensure_project_guide_files(root)
    role = detect_project_role(root) if root else "none"
    cwd = root or Path(project_path).resolve()
    base_lookups = default_lookups(root)
    welcome = welcome_text(root=root, role=role, executor=chosen_executor, locale=locale)

    def open_session(user_text: str) -> dict[str, Any]:
        lookups = lookups_for_message(root, user_text, base=base_lookups)
        handoff_path = _write_handoff(
            root=root,
            cwd=cwd,
            role=role,
            executor=chosen_executor,
            locale=locale,
            user_text=user_text,
            lookups=lookups,
        )
        launch_prompt = _handoff_launch_prompt(handoff_path, cwd=cwd, user_text=user_text, locale=locale)
        command = _session_command(executor=chosen_executor, cwd=cwd, initial_message=launch_prompt)
        env_updates = _session_env_updates(chosen_executor)
        payload = {
            "schema_version": "iteris.monitor_handoff_launch.v0",
            "user": user_text,
            "executor": chosen_executor,
            "project_path": str(root) if root else None,
            "cwd": str(cwd),
            "role": role,
            "handoff_path": str(handoff_path),
            "command": command,
            "env_updates": env_updates,
            "initial_message": launch_prompt,
            "lookups": lookups,
        }
        if json_output:
            return payload
        log.info(f"Opening {chosen_executor} with Iteris monitor context.")
        log.info(f"Handoff: {handoff_path}")
        if chosen_executor == "claude" and getattr(os, "geteuid", lambda: -1)() == 0:
            log.info("Claude root launch uses IS_SANDBOX=1 with --dangerously-skip-permissions.")
        env = {**os.environ, **env_updates}
        raise typer.Exit(subprocess.run(command, cwd=cwd, env=env, check=False).returncode)

    if welcome_only:
        payload = welcome_payload(root=root, role=role, executor=chosen_executor, locale=locale)
        if json_output:
            typer.echo(json.dumps(payload, indent=2, ensure_ascii=False))
        else:
            log.panel(welcome, title=t(locale, "choose_title"))
        return

    if json_output or message is not None or open_only:
        text = message if message is not None else opening_user_message(locale=locale, in_project=root is not None, role=role)
        payload = open_session(text)
        payload["welcome"] = welcome
        payload["locale"] = locale
        if json_output:
            typer.echo(json.dumps(payload, indent=2, ensure_ascii=False))
        else:
            typer.echo(f"Opening {payload['executor']} with handoff {payload['handoff_path']}")
        return

    log.header(t(locale, "monitor_title"))
    log.panel(welcome, title=t(locale, "choose_title"))

    exit_words = t(locale, "exit_words")
    if isinstance(exit_words, set):
        exit_set = exit_words
    else:
        exit_set = set(exit_words)

    while True:
        try:
            user_text = input(t(locale, "menu_input")).strip()
        except (EOFError, KeyboardInterrupt):
            typer.echo("")
            break
        if not user_text:
            continue
        if user_text.lower() in exit_set:
            break
        seeded = resolve_menu_input(user_text, role, locale=locale)
        if (seeded is None and user_text in {"1", "2", "3", "4"}) or (user_text.isdigit() and seeded == user_text):
            log.info(t(locale, "freeform_hint"))
            continue
        if seeded == WIZARD_ACTION:
            log.info(t(locale, "wizard_start"))
            run_new_project_wizard(cwd=cwd, locale=locale, executor=chosen_executor)
            continue
        llm_input = seeded if seeded is not None else user_text
        open_session(llm_input)

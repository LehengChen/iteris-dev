"""Public workflow commands for observing and controlling Iteris runs."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import typer

from iteris import log
from iteris.commands.common import require_public_project
from iteris.commands.context import build_context
from iteris.commands.goal import (
    attach_tmux_session,
    build_goal_finalize_report,
    capture_pane,
    codex_trust_prompt_present,
    latest_goal_logs,
    resolve_goal_defaults,
    stop_tmux_session,
    stop_verification_agents,
    tmux_session_exists,
)
from iteris.commands.logs import create_log_bundle
from iteris.frontier import frontier_health
from iteris.gitops import status as git_status
from iteris.liveness import scan_project_liveness
from iteris.project import read_json, require_project, session_slug, slugify
from iteris.verification.local import latest_results


def default_session_name(root: Path) -> str:
    state = current_run_state(root)
    if isinstance(state.get("session_name"), str) and state["session_name"]:
        return str(state["session_name"])
    return f"iteris-{session_slug(root.name)}"


def evolve_session_name(root: Path) -> str:
    return f"iteris-evolve-{session_slug(root.name)}"


def analyze_session_name(root: Path) -> str:
    return f"iteris-analyze-{session_slug(root.name)}"


def project_sessions(root: Path) -> list[dict[str, Any]]:
    """All known session kinds for this project with liveness.

    Stop semantics never cascade across kinds: `iteris stop` touches only the
    worker run; the evolve supervisor is stopped only by `iteris evolve stop`.
    """
    kinds = [
        ("run", default_session_name(root)),
        ("analyze", analyze_session_name(root)),
        ("evolve", evolve_session_name(root)),
    ]
    return [
        {"kind": kind, "session_name": name, "live": _session_exists(name)}
        for kind, name in kinds
    ]


def _resolve_session(root: Path, session: str | None, evolve: bool) -> str:
    if session and evolve:
        raise typer.BadParameter("pass either --session or --evolve, not both")
    if evolve:
        return evolve_session_name(root)
    return session or default_session_name(root)


def current_run_state(root: Path) -> dict[str, Any]:
    state = read_json(root / ".iteris" / "current_run.json", default={})
    return state if isinstance(state, dict) else {}


def status(
    project_path: str = typer.Argument(".", help="Iteris project path. Defaults to the current directory."),
    session: str | None = typer.Option(None, "--session", "-s", help="tmux session name."),
    json_output: bool = typer.Option(False, "--json", help="Print machine-readable JSON."),
) -> None:
    """Show project and run status."""
    root = require_public_project(project_path)
    session_name = session or default_session_name(root)
    problem_id, target_artifact = resolve_goal_defaults(root)
    session_live = _session_exists(session_name)
    run_state = _run_state(root, session_name=session_name, session_live=session_live)
    active = run_state == "running"
    context = build_context(root, limit=5)
    verifications = latest_results(root)
    target_path = root / target_artifact
    liveness = scan_project_liveness(root, session_name=session_name)
    payload = {
        "project_path": str(root),
        "session_name": session_name,
        "session_live": session_live,
        "sessions": project_sessions(root),
        "run_state": run_state,
        "run_active": active,
        "liveness": liveness,
        "fact_type_counts": context.get("fact_type_counts") or {},
        "attention": context.get("attention") or {},
        "source_file": context["source_file"],
        "target_artifact": target_artifact,
        "target": target_artifact,
        "target_exists": target_path.exists(),
        "facts_ok": context["facts_ok"],
        "fact_count": context["fact_count"],
        "ready_pool_tasks": len(context["ready_pool_tasks"]),
        "frontier_health": frontier_health(root),
        "verification_results": verifications[-5:],
        "git": git_status(root),
        "logs": latest_goal_logs(root, session_name),
    }
    if json_output:
        typer.echo(json.dumps(payload, indent=2, ensure_ascii=False))
        return
    live_others = [
        f"{item['kind']}:{item['session_name']}"
        for item in payload["sessions"]
        if item["live"] and item["session_name"] != session_name
    ]
    log.key_value(
        {
            "Project": payload["project_path"],
            "Run": run_state,
            "Session": session_name,
            "Other sessions": ", ".join(live_others) or "(none live)",
            "Agent runs": _agent_runs_summary(liveness),
            "Source": payload["source_file"] or "(none)",
            "Target": f"{target_artifact} ({'exists' if payload['target_exists'] else 'missing'})",
            "Facts": _facts_summary(payload),
            "Ready tasks": str(payload["ready_pool_tasks"]),
            "Frontier": "explore recommended" if payload["frontier_health"].get("explore_recommended") else "ok",
            "Attention": _attention_summary(payload["attention"]),
            "Git": _git_summary(payload["git"]),
        }
    )
    if liveness["needs_recovery"]:
        log.warn("Dead workers, orphaned tasks, or unharvested completed work detected; run `iteris recover` to consolidate.")
    _print_common_next_steps(active=active)


def attach(
    project_path: str = typer.Argument(".", help="Iteris project path. Defaults to the current directory."),
    session: str | None = typer.Option(None, "--session", "-s", help="tmux session name."),
    evolve: bool = typer.Option(False, "--evolve", help="Attach to the evolve supervisor session instead of the worker run."),
) -> None:
    """Attach to the live run terminal (worker by default)."""
    root = require_public_project(project_path)
    session_name = _resolve_session(root, session, evolve)
    if not _session_exists(session_name):
        live = [item for item in project_sessions(root) if item["live"]]
        hint = (
            "Live sessions: " + ", ".join(f"{item['kind']} ({item['session_name']})" for item in live)
            if live
            else "No live sessions. Use `iteris run` (worker) or `iteris evolve run` first."
        )
        raise typer.BadParameter(f"session not found: {session_name}. {hint}")
    log.info(_attach_hint())
    try:
        attach_tmux_session(session_name)
    except RuntimeError as exc:
        log.warn(str(exc))
        raise typer.Exit(1) from None


def stop(
    project_path: str = typer.Argument(".", help="Iteris project path. Defaults to the current directory."),
    session: str | None = typer.Option(None, "--session", "-s", help="tmux session name."),
    force_after: float = typer.Option(5.0, "--force-after", help="Seconds to wait after Ctrl-C before forcing tmux stop."),
    force: bool = typer.Option(True, "--force/--no-force", help="Force stop if the session stays active."),
    kill_verifiers: bool = typer.Option(True, "--kill-verifiers/--keep-verifiers", help="Also stop verification-agent processes for this project."),
    json_output: bool = typer.Option(False, "--json", help="Print machine-readable JSON."),
) -> None:
    """Stop a run session."""
    root = require_public_project(project_path)
    session_name = session or default_session_name(root)
    logs = latest_goal_logs(root, session_name)
    try:
        result = stop_tmux_session(session_name, force_after=force_after, force=force)
    except RuntimeError as exc:
        result = {"stopped": False, "active": False, "reason": str(exc), "actions": []}
    verifier_result = stop_verification_agents(root, force_after=force_after, force=force) if kill_verifiers else None
    payload = {"session_name": session_name, **result, "logs": logs, "verification_agents": verifier_result}
    if json_output:
        typer.echo(json.dumps(payload, indent=2, ensure_ascii=False))
        return
    if payload["stopped"]:
        log.success(f"Run stopped: {session_name}")
    elif payload["active"]:
        log.warn(f"Run still active: {session_name}")
    else:
        log.warn(f"Run not active: {session_name}")
    log.key_value({"Session": session_name, "Reason": str(payload["reason"]), "Pane log": logs.get("pane_log") or "(none)"})


def review(
    project_path: str = typer.Argument(".", help="Iteris project path. Defaults to the current directory."),
    session: str | None = typer.Option(None, "--session", "-s", help="tmux session name."),
    target_artifact: str | None = typer.Option(None, "--target-artifact", "-o", help="Terminal artifact path relative to the project."),
    bundle: bool = typer.Option(True, "--bundle/--no-bundle", help="Create a reproducibility log bundle."),
    json_output: bool = typer.Option(False, "--json", help="Print machine-readable JSON."),
) -> None:
    """Prepare or show files that reviewers should inspect."""
    root = require_public_project(project_path)
    session_name = session or default_session_name(root)
    _, target = resolve_goal_defaults(root, target_artifact=target_artifact)
    manifest = create_log_bundle(root, session=session_name) if bundle else None
    finalization = build_goal_finalize_report(root, target_artifact=target, require_clean=False)
    git_after_review = git_status(root)
    files = [
        "STATUS.md",
        target,
        "tasks/TASK_POOL.json",
        "memory/facts/",
        "memory/facts/FACT_INDEX.jsonl",
        "verification/results/",
        "artifacts/ARTIFACT_INDEX.jsonl",
        "artifacts/agent_runs/",
        "artifacts/proofs/",
        "artifacts/experiments/",
        "artifacts/code/",
        "artifacts/route_checks/",
        "artifacts/run_bundles/",
    ]
    payload = {
        "project_path": str(root),
        "session_name": session_name,
        "target_artifact": target,
        "verification_gate": finalization,
        "finalization": finalization,
        "bundle": manifest,
        "git_after_review": git_after_review,
        "review_files": files,
    }
    if json_output:
        typer.echo(json.dumps(payload, indent=2, ensure_ascii=False))
        return
    log.key_value(
        {
            "Project": str(root),
            "Target": target,
            "Verification gate": "passed" if finalization["ok"] else "not passed",
            "Run bundle": manifest["manifest_path"] if manifest else "(not created)",
            "Git after review": _git_summary(git_after_review),
        }
    )
    if git_after_review.get("dirty"):
        log.info('Checkpoint review artifacts with: iteris tool git checkpoint -m "checkpoint: archive review bundle"')
    if manifest and manifest.get("warnings"):
        for warning in manifest["warnings"]:
            log.warn(str(warning))
    log.results_table([(item, "ok" if (root / item).exists() or item.endswith("/") else "missing", "") for item in files], title="Review files")


def _session_exists(session_name: str) -> bool:
    try:
        return tmux_session_exists(session_name)
    except RuntimeError:
        return False


def _run_state(root: Path, *, session_name: str, session_live: bool) -> str:
    logs = latest_goal_logs(root, session_name)
    text = ""
    if session_live:
        try:
            text = capture_pane(session_name, lines=80)
        except RuntimeError:
            text = ""
    if not text:
        pane_log = logs.get("pane_log")
        text = _tail(Path(pane_log), lines=80) if pane_log else ""
    if session_live:
        return _run_state_from_text(text) if text else "running"
    # A dead session can only ever be achieved, stopped, or never started:
    # the pane-log fallback must not report "running" for a session that
    # no longer exists (post-crash status lied exactly this way).
    if text and _run_state_from_text(text) == "achieved":
        return "achieved"
    return "stopped" if text or logs.get("pane_log") else "not_active"


def _run_state_from_text(text: str) -> str:
    if codex_trust_prompt_present(text):
        return "waiting_for_codex_trust"
    if "Goal achieved" in text or "Goal usage:" in text or "Completed the Iteris goal contract." in text:
        return "achieved"
    return "running"


def _attach_hint() -> str:
    import os

    if os.environ.get("TMUX"):
        return "Already inside tmux; switching client to the Iteris run session."
    return "Detach from tmux with: Ctrl-b then d"


def _tail(path: Path, *, lines: int) -> str:
    if not path.exists():
        return ""
    return "\n".join(path.read_text(encoding="utf-8", errors="replace").splitlines()[-lines:])


def _agent_runs_summary(liveness: dict[str, Any]) -> str:
    live = liveness.get("live_agent_runs") or []
    orphaned = liveness.get("orphaned_agent_runs") or []
    if not live and not orphaned:
        return "(none active)"
    parts = []
    if live:
        labels = ", ".join(str(item.get("task_id") or item["run_id"]) for item in live[:3])
        parts.append(f"{len(live)} live ({labels})")
    if orphaned:
        parts.append(f"{len(orphaned)} orphaned")
    return "; ".join(parts)


def _facts_summary(payload: dict[str, Any]) -> str:
    base = f"{payload['fact_count']} ({'ok' if payload['facts_ok'] else 'invalid'})"
    counts = payload.get("fact_type_counts") or {}
    if len(counts) > 1 or (counts and "claim" not in counts):
        breakdown = ", ".join(f"{key} {value}" for key, value in sorted(counts.items()))
        return f"{base}: {breakdown}"
    return base


def _attention_summary(attention: dict[str, Any]) -> str:
    stale = attention.get("stale_tasks") or []
    streaks = attention.get("rejection_streaks") or []
    stale_verifications = attention.get("stale_verifications") or []
    keystones = attention.get("under_verified_keystones") or []
    status_lag = attention.get("status_md_stale_hours")
    parts = []
    if stale:
        parts.append(f"{len(stale)} stale running/review task(s)")
    if stale_verifications:
        dead = sum(1 for item in stale_verifications if item.get("verifier_process_alive") is False)
        parts.append(f"{len(stale_verifications)} verification request(s) without result" + (f", {dead} verifier(s) dead" if dead else ""))
    if streaks:
        worst = streaks[0]
        parts.append(f"{len(streaks)} repeated-rejection claim(s), worst {worst['consecutive_rejections']}x")
    if keystones:
        parts.append(f"{len(keystones)} under-verified keystone fact(s)")
    if status_lag is not None:
        parts.append(f"STATUS.md {status_lag}h stale")
    return "; ".join(parts) or "(none)"


def _git_summary(result: dict[str, Any]) -> str:
    if not result.get("repo"):
        return "not initialized"
    dirty = "dirty" if result.get("dirty") else "clean"
    return f"{result.get('branch')} ({dirty})"


def _print_common_next_steps(*, active: bool) -> None:
    commands = ["iteris monitor", "iteris dashboard", "iteris review"]
    if active:
        commands.extend(["iteris attach", "iteris stop"])
    else:
        commands.append("iteris run")
    log.panel("\n".join(f"  {cmd}" for cmd in commands), title="Useful commands")

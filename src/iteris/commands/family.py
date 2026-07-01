"""Family closure commands: joint sibling scheduling and shared verified pool."""

from __future__ import annotations

import json
import os
import shlex
import subprocess
import sys
import time
from pathlib import Path

import typer

from iteris import log
from iteris.family import (
    FamilyError,
    family_status,
    read_family_state,
    require_family_root,
    resolve_family_root_from_path,
    schedule_actions,
    sibling_by_id,
    start_sibling_run,
    stop_sibling_session,
    stop_watchdog_session,
    touch_watchdog_tick,
    watchdog_session_name,
    write_family_state,
)
from iteris.family_pool import export_verified_fact, list_pool_entries
from iteris.family_scaffold import parse_sibling_spec, perform_family_init, perform_family_new
from iteris.project import is_project, now_iso, read_json

app = typer.Typer(help="Joint scheduling and shared verified pool for sibling North-Star closure projects.")


def _family_root(path: str) -> Path:
    try:
        return require_family_root(Path(path))
    except FamilyError as exc:
        raise typer.BadParameter(str(exc)) from exc


@app.command("new")
def new_cmd(
    family_path: str = typer.Argument(".", help="Family wrapper directory to create or populate."),
    manifest: Path | None = typer.Option(None, "--manifest", help="JSON manifest with goal and siblings."),
    goal: str | None = typer.Option(None, "--goal", help="Family-level North-Star goal."),
    sibling: list[str] = typer.Option([], "--sibling", help="Sibling spec without manifest (requires source= in manifest)."),
    max_concurrent: int = typer.Option(2, "--max-concurrent", help="Parallel sibling runs."),
    json_output: bool = typer.Option(False, "--json", help="Print machine-readable JSON."),
) -> None:
    """Create a family wrapper and sibling Iteris projects."""
    siblings = [parse_sibling_spec(spec) for spec in sibling] if sibling else None
    try:
        payload = perform_family_new(
            Path(family_path),
            manifest_path=manifest,
            goal=goal,
            siblings=siblings,
            max_concurrent=max_concurrent,
        )
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc
    if json_output:
        typer.echo(json.dumps(payload, indent=2, ensure_ascii=False))
        return
    log.success(f"family created: {payload['siblings_created'].__len__()} sibling(s)")
    log.panel("  iteris family status .\n  iteris family schedule --dry-run .", title="Next steps")


@app.command("init")
def init_cmd(
    family_path: str = typer.Argument(".", help="Existing family wrapper directory."),
    goal: str = typer.Option(..., "--goal", help="Family-level North-Star goal."),
    sibling: list[str] = typer.Option(..., "--sibling", help="Sibling spec: id=...,path=...,session=...,target=...,gaps=A|B"),
    max_concurrent: int = typer.Option(2, "--max-concurrent", help="Parallel sibling runs."),
    adopt_symlinks: bool = typer.Option(False, "--adopt-symlinks", help="Register existing symlinked sibling paths."),
    claim_prefix: str | None = typer.Option(None, "--claim-prefix", help="Default claim prefix for closure detection."),
    json_output: bool = typer.Option(False, "--json", help="Print machine-readable JSON."),
) -> None:
    """Register an existing sibling layout under .iteris/FAMILY.json."""
    siblings = [parse_sibling_spec(spec) for spec in sibling]
    try:
        payload = perform_family_init(
            Path(family_path),
            goal=goal,
            siblings=siblings,
            max_concurrent=max_concurrent,
            adopt_symlinks=adopt_symlinks,
            claim_prefix=claim_prefix,
        )
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc
    if json_output:
        typer.echo(json.dumps(payload, indent=2, ensure_ascii=False))
        return
    log.success(f"family initialized: {payload['siblings']} sibling(s)")


@app.command("export")
def export_cmd(
    family_path: str = typer.Argument(".", help="Family wrapper root."),
    from_project: str = typer.Option(..., "--from", help="Sibling directory name or path relative to family root."),
    fact_id: str = typer.Option(..., "--fact-id", help="Verified fact id to export."),
    usable_by: str = typer.Option("", "--usable-by", help="Comma-separated sibling ids that may use this lead."),
    json_output: bool = typer.Option(False, "--json", help="Print machine-readable JSON."),
) -> None:
    """Export a sibling verified fact into the shared family pool."""
    family_root = _family_root(family_path)
    state = read_family_state(family_root)
    source = Path(from_project)
    if not source.is_absolute():
        source = (family_root / from_project).resolve()
    sibling_id = ""
    for sibling in state.get("siblings") or []:
        if not isinstance(sibling, dict):
            continue
        project = (family_root / str(sibling.get("path"))).resolve()
        if project == source.resolve():
            sibling_id = str(sibling.get("sibling_id"))
            break
    if not sibling_id:
        sibling_id = source.name
    usable = [item.strip() for item in usable_by.split(",") if item.strip()]
    try:
        entry = export_verified_fact(
            family_root,
            source_project=source,
            fact_id=fact_id,
            source_sibling_id=sibling_id,
            usable_by=usable,
        )
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc
    if json_output:
        typer.echo(json.dumps(entry, indent=2, ensure_ascii=False))
        return
    log.success(f"exported {fact_id} for siblings: {', '.join(usable) or '(all)'}")


@app.command("pool")
def pool_cmd(
    family_path: str = typer.Argument(".", help="Family wrapper root."),
    sibling: str | None = typer.Option(None, "--sibling", help="Filter entries usable by this sibling id."),
    json_output: bool = typer.Option(False, "--json", help="Print machine-readable JSON."),
) -> None:
    """List shared family pool entries."""
    family_root = _family_root(family_path)
    rows = list_pool_entries(family_root, sibling_id=sibling)
    if json_output:
        typer.echo(json.dumps(rows, indent=2, ensure_ascii=False))
        return
    table_rows = [
        (
            str(row.get("origin_fact_id") or "(missing)"),
            str(row.get("source_sibling_id") or row.get("source_project") or "-"),
            str(row.get("claim_summary") or row.get("curated_summary") or "")[:120],
        )
        for row in rows
    ]
    log.results_table(table_rows or [("empty", "-", "no pool entries yet")], title="Family pool")


@app.command("status")
def status_cmd(
    family_path: str = typer.Argument(".", help="Family wrapper root or sibling path."),
    json_output: bool = typer.Option(False, "--json", help="Print machine-readable JSON."),
) -> None:
    """Show sibling phases, sessions, and TASK_POOL summaries."""
    family_root = _family_root(family_path)
    payload = family_status(family_root)
    if json_output:
        typer.echo(json.dumps(payload, indent=2, ensure_ascii=False))
        return
    log.key_value(
        {
            "family_root": payload["family_root"],
            "goal": str(payload.get("goal") or "")[:120],
            "running_open": str(payload.get("running_open")),
            "max_concurrent": str(payload.get("max_concurrent")),
        }
    )
    table_rows = [
        (
            str(row.get("sibling_id") or "?"),
            f"{row.get('phase')}/{'live' if row.get('session_live') else 'down'}",
            f"{row.get('session')} | ready={row.get('ready_tasks')} align={row.get('alignment_ready_tasks')} | {row.get('active_frontier') or '-'}",
        )
        for row in payload.get("siblings") or []
    ]
    log.results_table(table_rows or [("none", "-", "no siblings")], title="Family siblings")


@app.command("schedule")
def schedule_cmd(
    family_path: str = typer.Argument(".", help="Family wrapper root."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Compute start actions without launching runs."),
    json_output: bool = typer.Option(False, "--json", help="Print machine-readable JSON."),
) -> None:
    """Run one scheduling tick: start open siblings up to max_concurrent."""
    family_root = _family_root(family_path)
    actions = schedule_actions(family_root, dry_run=dry_run)
    if json_output:
        typer.echo(json.dumps(actions, indent=2, ensure_ascii=False))
        return
    rows = [
        (
            str(action.get("sibling_id") or "?"),
            "dry-run" if dry_run else ("ok" if action.get("ok", True) else "error"),
            " ".join(shlex.quote(part) for part in action.get("command") or [])[:160],
        )
        for action in actions
    ]
    log.results_table(rows or [("none", "idle", "no start actions")], title="Schedule actions")


@app.command("start")
def start_cmd(
    family_path: str = typer.Argument(".", help="Family wrapper root."),
    sibling: str = typer.Option(..., "--sibling", help="Sibling id to start."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Print launch command without starting."),
    json_output: bool = typer.Option(False, "--json", help="Print machine-readable JSON."),
) -> None:
    """Manually start one sibling /goal loop."""
    family_root = _family_root(family_path)
    state = read_family_state(family_root)
    try:
        entry = sibling_by_id(state, sibling)
    except FamilyError as exc:
        raise typer.BadParameter(str(exc)) from exc
    action = start_sibling_run(family_root, entry, state, dry_run=dry_run)
    if json_output:
        typer.echo(json.dumps(action, indent=2, ensure_ascii=False))
        return
    if dry_run:
        log.info(" ".join(shlex.quote(part) for part in action.get("command") or []))
    elif action.get("ok"):
        log.success(f"started sibling {sibling}: {action.get('session')}")
    else:
        raise typer.Exit(1)


@app.command("run")
def run_cmd(
    family_path: str = typer.Argument(".", help="Family wrapper root."),
    foreground: bool = typer.Option(False, "--foreground", help="Run the supervisor loop in this terminal."),
    tick_seconds: int = typer.Option(1800, "--tick-seconds", help="Seconds between schedule ticks."),
    json_output: bool = typer.Option(False, "--json", help="Print machine-readable JSON."),
) -> None:
    """Start a detached tmux supervisor that periodically schedules sibling runs."""
    family_root = _family_root(family_path)
    state = read_family_state(family_root)
    session_name = watchdog_session_name(family_root, state)
    run = dict(state.get("run") or {})
    if not run.get("started_at"):
        run["started_at"] = now_iso()
        state["run"] = run
        write_family_state(family_root, state)

    if foreground:
        typer.echo(
            f"family supervisor loop: tick every {tick_seconds}s; Ctrl-C or `iteris family stop --supervisor` to stop."
        )
        ticks = 0
        try:
            while True:
                touch_watchdog_tick(family_root)
                schedule_actions(family_root)
                ticks += 1
                time.sleep(max(1, tick_seconds))
        except KeyboardInterrupt:
            payload = {"mode": "foreground", "ticks": ticks, "session_name": session_name}
            if json_output:
                typer.echo(json.dumps(payload, indent=2, ensure_ascii=False))
            else:
                log.success(f"family supervisor stopped after {ticks} tick(s)")
            return

    from iteris.family import session_live

    if session_live(session_name):
        raise typer.BadParameter(f"family supervisor already running: {session_name}")
    inner = (
        f"{shlex.quote(sys.executable)} -m iteris.cli family run {shlex.quote(str(family_root))} "
        f"--foreground --tick-seconds {tick_seconds}"
    )
    subprocess.run(["tmux", "new-session", "-d", "-s", session_name, inner], check=True)
    payload = {"mode": "tmux", "session_name": session_name, "tick_seconds": tick_seconds}
    if json_output:
        typer.echo(json.dumps(payload, indent=2, ensure_ascii=False))
        return
    log.success(f"family supervisor started: {session_name}")


@app.command("stop")
def stop_cmd(
    family_path: str = typer.Argument(".", help="Family wrapper root."),
    sibling: str | None = typer.Option(None, "--sibling", help="Stop one sibling worker session."),
    supervisor: bool = typer.Option(False, "--supervisor", help="Stop the family supervisor session."),
    json_output: bool = typer.Option(False, "--json", help="Print machine-readable JSON."),
) -> None:
    """Stop sibling worker sessions or the family supervisor."""
    family_root = _family_root(family_path)
    state = read_family_state(family_root)
    payload: dict[str, object] = {}
    if supervisor:
        payload["supervisor"] = stop_watchdog_session(family_root, state)
    if sibling:
        entry = sibling_by_id(state, sibling)
        payload["sibling"] = stop_sibling_session(family_root, entry)
    if not supervisor and not sibling:
        raise typer.BadParameter("pass --sibling <id> and/or --supervisor")
    if json_output:
        typer.echo(json.dumps(payload, indent=2, ensure_ascii=False))
        return
    if supervisor and payload.get("supervisor", {}).get("stopped"):
        log.success("family supervisor stopped")
    if sibling:
        log.success(f"sibling {sibling} session stop requested")

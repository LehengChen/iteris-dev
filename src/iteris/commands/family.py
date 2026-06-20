"""Family closure: scaffold, shared pool, and joint scheduling."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import typer

from iteris import log
from iteris.family import (
    FamilyError,
    family_session_name,
    family_status,
    has_family_state,
    init_state,
    read_family_marker,
    read_state,
    read_watchdog_goal,
    resolve_family_root as resolve_family_path,
    resolve_sibling_path,
    schedule_actions,
    schedule_tick,
    start_sibling_run,
    write_state,
)
from iteris.memory.family import resolve_family_root
from iteris.family_pool import applicable_pool_entries, export_verified_fact, load_pool
from iteris.family_scaffold import create_family, load_manifest, manifest_to_create_args
from iteris.project import now_iso

app = typer.Typer(
    help="Family closure workspace: scaffold siblings, shared verified-fact pool, joint scheduling."
)


def _root(project_path: str) -> Path:
    return resolve_family_path(project_path)


def _tmux_session_exists(name: str) -> bool:
    try:
        from iteris.tmux import tmux_session_alive

        return tmux_session_alive(name)
    except Exception:
        return False


def _parse_sibling_spec(raw: str) -> dict[str, str]:
    parts: dict[str, str] = {}
    for piece in raw.split(","):
        key, _, value = piece.partition("=")
        key = key.strip()
        value = value.strip()
        if key and value:
            parts[key] = value
    sid = parts.get("id") or parts.get("sibling_id") or parts.get("path") or parts.get("dir")
    if not sid:
        raise typer.BadParameter(f"invalid --sibling (need id=): {raw}")
    entry: dict[str, str] = {
        "sibling_id": sid,
        "path": parts.get("path") or parts.get("dir") or sid,
    }
    for key in ("session", "target", "source", "north_star", "north_star_file", "title", "claim_prefix"):
        if parts.get(key):
            entry[key] = parts[key]
    if parts.get("target"):
        entry["target_artifact"] = parts["target"]
    if parts.get("gaps"):
        entry["gaps"] = [g.strip() for g in parts["gaps"].replace("|", ",").split(",") if g.strip()]
    if parts.get("priority"):
        entry["priority"] = parts["priority"]
    return entry


@app.command("new")
def family_new(
    project_path: str = typer.Argument(".", help="New family root directory to create."),
    manifest: str | None = typer.Option(None, "--manifest", "-m", help="JSON manifest with goal + siblings."),
    goal: str | None = typer.Option(None, "--goal", "-g", help="Family north-star headline."),
    sibling: list[str] = typer.Option(None, "--sibling", help="Sibling spec (repeatable). See `family init --help`."),
    max_concurrent: int = typer.Option(2, "--max-concurrent", help="Parallel sibling /goal loops."),
    json_output: bool = typer.Option(False, "--json", help="Print machine-readable JSON."),
) -> None:
    """Create a family workspace: shared pool + N sibling Iteris projects."""
    family_root = Path(project_path).resolve()
    if manifest:
        manifest_path = Path(manifest).resolve()
        args = manifest_to_create_args(load_manifest(manifest_path))
    else:
        if not goal:
            raise typer.BadParameter("pass --goal or --manifest")
        sibs = [_parse_sibling_spec(item) for item in (sibling or [])]
        if not sibs:
            raise typer.BadParameter("pass at least one --sibling or use --manifest")
        args = {
            "goal": goal,
            "siblings": sibs,
            "schedule": {"max_concurrent": max_concurrent},
            "policy": {},
            "shared_references": None,
            "source_problems_doc": None,
        }
    try:
        state = create_family(family_root, **args)
    except FamilyError as exc:
        raise typer.BadParameter(str(exc)) from exc
    payload = {
        "family_root": str(family_root),
        "goal": state.get("goal"),
        "siblings": [s["sibling_id"] for s in state.get("siblings", [])],
        "pool": state.get("pool"),
    }
    if json_output:
        typer.echo(json.dumps(payload, indent=2, ensure_ascii=False))
        return
    log.success(f"family created: {len(state['siblings'])} siblings at {family_root}")
    log.panel(
        "  iteris family status\n  iteris family start\n  iteris family export . --from <sibling> --fact-id <fact:id>",
        title="Next steps",
    )


@app.command("init")
def init(
    project_path: str = typer.Argument(".", help="Family wrapper directory (may contain sibling symlinks)."),
    goal: str = typer.Option(..., "--goal", "-g", help="Family-level north-star headline."),
    sibling: list[str] = typer.Option(
        None,
        "--sibling",
        help="Sibling spec id=NAME,path=REL,session=SESSION,target=ARTIFACT,gaps=GAP1|GAP2 (repeatable).",
    ),
    max_concurrent: int = typer.Option(2, "--max-concurrent", help="Parallel sibling /goal loops."),
    adopt_symlinks: bool = typer.Option(True, "--adopt-symlinks/--no-adopt-symlinks", help="Auto-adopt iteris-project symlinks under the family root."),
    allow_principled_stop: bool = typer.Option(False, "--allow-principled-stop/--no-principled-stop", help="Treat certified principled_stop as terminal."),
    claim_prefix: str | None = typer.Option(None, "--claim-prefix", help="Required goal_success claim prefix for all siblings."),
    json_output: bool = typer.Option(False, "--json", help="Print machine-readable JSON."),
) -> None:
    """Register an existing family layout (adopt symlinks / manual siblings)."""
    root = _root(project_path)
    siblings = [_parse_sibling_spec(item) for item in (sibling or [])]
    try:
        state = init_state(
            root,
            goal=goal,
            siblings=siblings or None,
            schedule={"max_concurrent": max_concurrent},
            policy={
                "allow_principled_stop": allow_principled_stop,
                "claim_prefix": claim_prefix,
            },
            adopt_symlinks=adopt_symlinks,
        )
    except FamilyError as exc:
        raise typer.BadParameter(str(exc)) from exc
    payload = {
        "family_state": str(root / ".iteris" / "FAMILY.json"),
        "goal": state.get("goal"),
        "siblings": [s["sibling_id"] for s in state.get("siblings", [])],
        "schedule": state.get("schedule"),
    }
    if json_output:
        typer.echo(json.dumps(payload, indent=2, ensure_ascii=False))
        return
    log.success(f"family initialized: {len(state['siblings'])} siblings")
    log.panel(
        "  iteris family status\n  iteris family schedule --dry-run\n  iteris family start",
        title="Next steps",
    )


@app.command("export")
def export_fact(
    project_path: str = typer.Argument(".", help="Family root directory."),
    from_path: str = typer.Option(..., "--from", help="Sibling project path exporting the fact."),
    fact_id: str = typer.Option(..., "--fact-id", help="Verified fact id to export."),
    usable_by: list[str] = typer.Option(None, "--usable-by", help="Sibling ids that may cite this fact (repeatable). Default: all other siblings."),
    json_output: bool = typer.Option(False, "--json", help="Print machine-readable JSON."),
) -> None:
    """Export a sibling verified fact into the family shared pool."""
    family_root = _root(project_path)
    if not has_family_state(family_root):
        raise typer.BadParameter("run `iteris family new` or `family init` first")
    sibling_root = Path(from_path).resolve()
    marker = read_family_marker(sibling_root)
    if not marker or str(marker.get("family_root")) != str(family_root.resolve()):
        raise typer.BadParameter(f"{from_path} is not a sibling of {family_root}")
    sibling_id = str(marker.get("sibling_id") or "")
    state = read_state(family_root)
    others = [s["sibling_id"] for s in state.get("siblings", []) if s.get("sibling_id") != sibling_id]
    targets = usable_by if usable_by else others
    try:
        entry = export_verified_fact(
            family_root,
            sibling_root,
            fact_id=fact_id,
            sibling_id=sibling_id,
            usable_by=targets,
        )
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc
    if json_output:
        typer.echo(json.dumps(entry, indent=2, ensure_ascii=False))
        return
    log.success(f"exported {fact_id} to family pool (usable_by={targets})")


@app.command("pool")
def pool_status(
    project_path: str = typer.Argument(".", help="Family root or sibling project."),
    sibling_id: str | None = typer.Option(None, "--sibling", help="Filter entries usable by this sibling."),
    json_output: bool = typer.Option(False, "--json", help="Print machine-readable JSON."),
) -> None:
    """List shared family pool entries."""
    root = Path(project_path).resolve()
    family_root = resolve_family_root(root)
    if family_root is None:
        raise typer.BadParameter("not inside a family closure workspace")
    sid = sibling_id
    if sid is None:
        marker = read_family_marker(root)
        if marker:
            sid = marker.get("sibling_id")
    entries = applicable_pool_entries(family_root, sibling_id=sid, limit=50)
    payload = {"family_root": str(family_root), "sibling_id": sid, "entries": entries, "total": len(load_pool(family_root))}
    if json_output:
        typer.echo(json.dumps(payload, indent=2, ensure_ascii=False))
        return
    log.key_value({"Pool": str(family_root / "memory" / "family" / "FAMILY_INDEX.jsonl"), "Total": str(payload["total"]), "Shown": str(len(entries))})
    rows = []
    for row in entries:
        src = row.get("source_sibling_id") or row.get("source_project") or "?"
        fid = row.get("origin_fact_id") or row.get("fact_id") or "?"
        summary = str(row.get("claim_summary") or "")[:80]
        rows.append((str(src), str(fid)[-40:], summary))
    if rows:
        log.results_table(rows, title="Pool entries")


@app.command("status")
def status(
    project_path: str = typer.Argument(".", help="Family root directory."),
    json_output: bool = typer.Option(False, "--json", help="Print machine-readable JSON."),
) -> None:
    """Sibling phases, sessions, pool frontiers, and scheduling slots."""
    root = _root(project_path)
    if not has_family_state(root):
        raise typer.BadParameter(f"no family state at {root / '.iteris' / 'FAMILY.json'}; run `iteris family new` first")
    payload = family_status(root)
    payload["supervisor_session"] = family_session_name(root)
    payload["supervisor_live"] = _tmux_session_exists(payload["supervisor_session"])
    payload["pool_total"] = len(load_pool(root))
    if json_output:
        typer.echo(json.dumps(payload, indent=2, ensure_ascii=False))
        return
    summary = payload["summary"]
    log.key_value(
        {
            "Goal": str(payload.get("goal", ""))[:120],
            "Siblings": f"{summary['closed']}/{summary['total']} closed, {summary['running']} running",
            "Pool": f"{payload['pool_total']} exported facts",
            "Slots": f"{summary['slots_available']} available (max {payload['schedule'].get('max_concurrent')})",
        }
    )
    rows = []
    for s in payload["siblings"]:
        phase = s["phase"]
        live = "live" if s["session_live"] else "stopped"
        align = ", ".join(s["pool"].get("ready_alignment_tasks")[:2]) or "—"
        detail = f"{live} · {s['pool'].get('active_frontier') or '—'} · {align}"
        rows.append((s["sibling_id"], phase, detail))
    log.results_table(rows, title="Siblings")


@app.command("schedule")
def schedule(
    project_path: str = typer.Argument(".", help="Family root directory."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Show planned starts without launching runs."),
    json_output: bool = typer.Option(False, "--json", help="Print machine-readable JSON."),
) -> None:
    """One scheduling tick: start sibling runs up to max_concurrent."""
    root = _root(project_path)
    if not has_family_state(root):
        raise typer.BadParameter("run `iteris family new` first")
    result = schedule_tick(root, dry_run=dry_run)
    if json_output:
        typer.echo(json.dumps(result, indent=2, ensure_ascii=False))
        return
    actions = result.get("actions") or []
    if not actions:
        log.info("No sibling starts needed (all open slots filled or siblings closed).")
        return
    for action in actions:
        log.info(f"{'would start' if dry_run else 'started'} {action['sibling_id']} session={action['session']}")


@app.command("start")
def start(
    project_path: str = typer.Argument(".", help="Family root directory."),
    sibling_id: str | None = typer.Option(None, "--sibling", help="Start only this sibling."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Print commands without launching."),
    json_output: bool = typer.Option(False, "--json", help="Print machine-readable JSON."),
) -> None:
    """Start sibling /goal loops (respects max_concurrent unless --sibling is set)."""
    root = _root(project_path)
    if not has_family_state(root):
        raise typer.BadParameter("run `iteris family new` first")
    actions = schedule_actions(root)
    if sibling_id:
        actions = [a for a in actions if a.get("sibling_id") == sibling_id]
        if not actions:
            state = read_state(root)
            entry = next((s for s in state.get("siblings", []) if s.get("sibling_id") == sibling_id), None)
            if not entry:
                raise typer.BadParameter(f"unknown sibling: {sibling_id}")
            sibling_root = resolve_sibling_path(root, entry)
            goal = read_watchdog_goal(sibling_root)
            actions = [
                {
                    "action": "start_run",
                    "sibling_id": sibling_id,
                    "project_path": str(sibling_root),
                    "session": entry.get("session") or f"iteris-{sibling_root.name}",
                    "target_artifact": entry.get("target_artifact"),
                    "goal": goal,
                }
            ]
    outcomes = []
    for action in actions:
        outcomes.append(start_sibling_run(action, dry_run=dry_run))
    if not dry_run:
        state = read_state(root)
        state.setdefault("run", {})["started_at"] = state.get("run", {}).get("started_at") or now_iso()
        write_state(root, state)
    payload = {"actions": actions, "outcomes": outcomes, "dry_run": dry_run}
    if json_output:
        typer.echo(json.dumps(payload, indent=2, ensure_ascii=False))
        return
    for action in actions:
        log.success(f"{'would start' if dry_run else 'started'} {action['sibling_id']}")


@app.command("run")
def run_supervisor(
    project_path: str = typer.Argument(".", help="Family root directory."),
    foreground: bool = typer.Option(False, "--foreground", help="Run scheduling loop in this terminal."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Journal starts without launching runs."),
    ticks: int | None = typer.Option(None, "--ticks", help="Stop after N schedule ticks."),
    tick_seconds: int = typer.Option(900, "--tick-seconds", help="Seconds between schedule ticks."),
    json_output: bool = typer.Option(False, "--json", help="Print machine-readable JSON."),
) -> None:
    """Detached tmux supervisor that periodically schedules sibling runs."""
    root = _root(project_path)
    if not has_family_state(root):
        raise typer.BadParameter("run `iteris family new` first")
    session_name = family_session_name(root)
    if foreground or dry_run:
        import time

        count = 0
        summaries = []
        while True:
            result = schedule_tick(root, dry_run=dry_run)
            summaries.append(result)
            count += 1
            if ticks is not None and count >= ticks:
                break
            if dry_run:
                break
            time.sleep(tick_seconds)
        if json_output:
            typer.echo(json.dumps({"ticks": summaries}, indent=2, ensure_ascii=False))
        return
    if _tmux_session_exists(session_name):
        log.warn(f"Family supervisor session already exists: {session_name}")
        raise typer.Exit(1)
    cmd = [
        sys.executable,
        "-m",
        "iteris.cli",
        "family",
        "run",
        str(root),
        "--foreground",
        "--tick-seconds",
        str(tick_seconds),
    ]
    from iteris.commands.goal.session import build_tmux_shell_command

    shell_cmd = " ".join(cmd)
    launch = build_tmux_shell_command(session_name)
    import subprocess

    subprocess.run(launch, check=True)
    subprocess.run(["tmux", "respawn-pane", "-t", session_name, "-k", shell_cmd], check=True)
    state = read_state(root)
    state.setdefault("run", {})["started_at"] = now_iso()
    state["run"]["supervisor_session"] = session_name
    write_state(root, state)
    if json_output:
        typer.echo(json.dumps({"session_name": session_name, "started": True}, indent=2))
    else:
        log.success(f"family supervisor started: {session_name}")


@app.command("stop")
def stop(
    project_path: str = typer.Argument(".", help="Family root directory."),
    sibling_id: str | None = typer.Option(None, "--sibling", help="Stop only this sibling's session."),
    supervisor: bool = typer.Option(False, "--supervisor", help="Stop the family supervisor session."),
    json_output: bool = typer.Option(False, "--json", help="Print machine-readable JSON."),
) -> None:
    """Stop sibling or family supervisor tmux sessions."""
    root = _root(project_path)
    if not has_family_state(root):
        raise typer.BadParameter("no family state")
    state = read_state(root)
    stopped: list[str] = []
    if supervisor:
        name = family_session_name(root)
        if _tmux_session_exists(name):
            import subprocess

            subprocess.run(["tmux", "kill-session", "-t", name], check=True)
            stopped.append(name)
    targets = state.get("siblings") or []
    if sibling_id:
        targets = [s for s in targets if s.get("sibling_id") == sibling_id]
    for entry in targets:
        if not isinstance(entry, dict):
            continue
        session = str(entry.get("session") or "")
        if session and _tmux_session_exists(session):
            import subprocess

            subprocess.run(["tmux", "kill-session", "-t", session], check=True)
            stopped.append(session)
    payload = {"stopped": stopped}
    if json_output:
        typer.echo(json.dumps(payload, indent=2, ensure_ascii=False))
        return
    if stopped:
        log.success(f"stopped: {', '.join(stopped)}")
    else:
        log.info("no live sessions to stop")

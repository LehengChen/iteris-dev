"""Evolve commands: the family's master supervisor."""

from __future__ import annotations

import json
import os
import shlex
import subprocess
import sys
from pathlib import Path

import typer

from iteris import log
from iteris.commands.workflow import evolve_session_name
from iteris.executors import resolve_executor
from iteris.evolve import (
    EvolveError,
    budget_status,
    has_evolve_state,
    init_state,
    propose_direction as propose_direction_state,
    read_state,
    unseeded_open,
    veto_direction as veto_direction_state,
    write_state,
)
from iteris.project import now_iso, require_project

app = typer.Typer(help="Budget-bounded generalization of a verified result across a project family.")


def _root(project_path: str) -> Path:
    return require_project(project_path)


@app.command("init")
def init(
    project_path: str = typer.Argument(".", help="Family root project (holds the verified result)."),
    goal: str = typer.Option(..., "--goal", help="The family-level goal, e.g. 'push the bound to the most general kernel class'."),
    budget_hours: float = typer.Option(72.0, "--budget-hours", help="Wall-clock budget for the whole evolve run."),
    max_concurrent: int = typer.Option(2, "--max-concurrent", help="Parallel child runs."),
    node_stall_hours: float = typer.Option(18.0, "--node-stall-hours", help="Hours without progress before a node counts as stalled."),
    max_nodes: int = typer.Option(12, "--max-nodes", help="Hard cap on family size."),
    analysis_directions: int = typer.Option(3, "--analysis-directions", help="Directions requested per verified node analysis."),
    veto_minutes: int = typer.Option(60, "--veto-minutes", help="Human veto window for new directions (0 disables)."),
    json_output: bool = typer.Option(False, "--json", help="Print machine-readable JSON."),
) -> None:
    """Create generalize/EVOLVE.json, adopting existing family projects as nodes."""
    root = _root(project_path)
    if analysis_directions < 1:
        raise typer.BadParameter("--analysis-directions must be at least 1")
    try:
        state = init_state(
            root,
            goal=goal,
            budget={
                "wall_hours": budget_hours,
                "max_concurrent": max_concurrent,
                "node_stall_hours": node_stall_hours,
                "max_nodes": max_nodes,
            },
            policy={
                "analysis_directions_per_node": analysis_directions,
                "seed_veto_window_minutes": veto_minutes,
            },
        )
    except EvolveError as exc:
        raise typer.BadParameter(str(exc)) from exc
    (root / "memory" / "family").mkdir(parents=True, exist_ok=True)
    payload = {
        "evolve_state": str(root / "generalize" / "EVOLVE.json"),
        "nodes": [n["node_id"] for n in state["nodes"]],
        "directions": len(state["direction_pool"]),
        "budget": state["budget"],
        "policy": state["policy"],
    }
    if json_output:
        typer.echo(json.dumps(payload, indent=2, ensure_ascii=False))
        return
    log.success(f"evolve initialized: {len(state['nodes'])} nodes adopted, "
                f"{len(state['direction_pool'])} directions in the pool")
    log.panel("  iteris evolve status\n  iteris evolve run --dry-run --ticks 1\n  iteris evolve run", title="Next steps")


@app.command("run")
def run(
    project_path: str = typer.Argument(".", help="Family root project."),
    foreground: bool = typer.Option(False, "--foreground", help="Run the supervisor loop in this terminal instead of detached tmux."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Full observe/judge cycle; actuators journal intents without executing."),
    ticks: int | None = typer.Option(None, "--ticks", help="Stop after N ticks (mostly with --dry-run)."),
    tick_seconds: int = typer.Option(1800, "--tick-seconds", help="Seconds between supervisor ticks."),
    executor: str | None = typer.Option(None, "--executor", "-e", help="Agent CLI for the whole family (master judges + children): codex or claude. Defaults to $ITERIS_EXECUTOR, then codex."),
    verification_executor: str | None = typer.Option(None, "--verification-executor", help="Verifier CLI for the family. Defaults to $ITERIS_VERIFICATION_EXECUTOR, else follows --executor."),
    json_output: bool = typer.Option(False, "--json", help="Print machine-readable JSON."),
) -> None:
    """Start the evolve master supervisor (detached tmux by default)."""
    root = _root(project_path)
    # Resolve the executor IN THE LAUNCHING PROCESS, which still has the shell's
    # env. The detached tmux session inherits the long-running tmux SERVER's
    # environment (NOT this process's), so $ITERIS_EXECUTOR would otherwise be
    # dropped and the master + judges + children would all fall back to codex
    # (FI-0036). We bake the resolved value into the tmux command and os.environ.
    try:
        executor_name = resolve_executor(executor)
        verification_source = verification_executor or os.environ.get("ITERIS_VERIFICATION_EXECUTOR")
        verification_executor_name = resolve_executor(verification_source) if verification_source else None
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from None
    state = read_state(root)
    if not state.get("run", {}).get("started_at"):
        state.setdefault("run", {})["started_at"] = now_iso()
        write_state(root, state)

    if foreground or dry_run:
        from iteris.supervision.engine import run_loop
        from iteris.supervision.profiles.evolve import build_profile

        # In-process: pin the executor into os.environ so the judgment backend
        # (agent_backend) and every run_cli child (generalize analyze, child
        # `iteris run`) inherit it. Idempotent when the detached path already
        # baked it into the pane env.
        os.environ["ITERIS_EXECUTOR"] = executor_name
        if verification_executor_name:
            os.environ["ITERIS_VERIFICATION_EXECUTOR"] = verification_executor_name

        profile = build_profile(root, tick_seconds=tick_seconds)

        def _heartbeat(tick_no: int, summary) -> None:
            stamp = now_iso()[:19]
            if summary.idle:
                detail = "idle (no triggers; pool waiting or nothing new)"
            else:
                parts = []
                if summary.fired:
                    parts.append("fired: " + ", ".join(summary.fired))
                for judgment in summary.judgments:
                    parts.append(
                        f"judge {judgment['contract']}: {'ok' if judgment['ok'] else 'FAILED'}"
                    )
                for act in summary.actions:
                    parts.append(f"act {act['action']}: {'ok' if act['ok'] else 'FAILED'}")
                detail = "; ".join(parts)
            typer.echo(f"[{stamp}] tick {tick_no}: {detail}", err=False)

        typer.echo(
            f"evolve supervisor loop: tick every {tick_seconds}s; behavior stream in "
            ".iteris/supervision/journal.jsonl; Ctrl-C or `iteris evolve stop` to stop."
        )
        executed = run_loop(root, profile, dry_run=dry_run, max_ticks=ticks, on_tick=_heartbeat)
        payload = {"mode": "foreground", "ticks": executed, "dry_run": dry_run, "executor": executor_name}
        if json_output:
            typer.echo(json.dumps(payload, indent=2, ensure_ascii=False))
        else:
            log.success(f"evolve loop finished after {executed} tick(s)")
        return

    session_name = evolve_session_name(root)
    if _tmux_session_exists(session_name):
        raise typer.BadParameter(
            f"evolve session already exists: {session_name}. Use `iteris evolve stop` first."
        )
    # Bake the executor into the pane command itself (an `env` prefix plus an
    # explicit --executor), so the detached master carries it regardless of the
    # tmux server's environment — the FI-0036 fix.
    env_pairs = {"ITERIS_EXECUTOR": executor_name}
    if verification_executor_name:
        env_pairs["ITERIS_VERIFICATION_EXECUTOR"] = verification_executor_name
    env_prefix = "env " + " ".join(f"{k}={shlex.quote(v)}" for k, v in env_pairs.items()) + " "
    exec_flags = f" --executor {shlex.quote(executor_name)}"
    if verification_executor_name:
        exec_flags += f" --verification-executor {shlex.quote(verification_executor_name)}"
    inner = (
        f"{env_prefix}{shlex.quote(sys.executable)} -m iteris.cli evolve run {shlex.quote(str(root))} "
        f"--foreground --tick-seconds {tick_seconds}{exec_flags}"
    )
    subprocess.run(["tmux", "new-session", "-d", "-s", session_name, inner], check=True)
    payload = {"mode": "tmux", "session_name": session_name, "tick_seconds": tick_seconds, "executor": executor_name}
    if json_output:
        typer.echo(json.dumps(payload, indent=2, ensure_ascii=False))
        return
    log.success(f"evolve supervisor started: {session_name}")
    log.panel(
        "  iteris evolve status\n  iteris monitor\n  iteris dashboard\n  iteris attach --evolve\n  iteris evolve stop",
        title="Observe and control",
    )


@app.command("status")
def status(
    project_path: str = typer.Argument(".", help="Family root project."),
    json_output: bool = typer.Option(False, "--json", help="Print machine-readable JSON."),
) -> None:
    """Family tree, direction pool, budget, and pending vetoes."""
    root = _root(project_path)
    state = read_state(root)
    budget = budget_status(state)
    pool = state.get("direction_pool", [])
    pending_veto = [e for e in pool if e.get("status") == "proposed" and e.get("vetoable_until")]
    unseeded = unseeded_open(state)
    substance = _substance_summary(root)
    payload = {
        "goal": state.get("goal"),
        "session_name": evolve_session_name(root),
        "session_live": _tmux_session_exists(evolve_session_name(root)),
        "budget": budget,
        "nodes": state.get("nodes", []),
        "direction_pool": pool,
        "pending_veto": [e["direction_id"] for e in pending_veto],
        "unseeded_directions": unseeded,
        "substance": substance,
        "boundary": state.get("boundary", []),
    }
    if json_output:
        typer.echo(json.dumps(payload, indent=2, ensure_ascii=False))
        return
    if pending_veto:
        log.warn("Pending veto window:")
        for entry in pending_veto:
            log.info(f"  {entry['direction_id']} (until {entry.get('vetoable_until')}) — veto with: "
                     f"iteris evolve veto {entry['direction_id']}")
    if unseeded and not payload["session_live"]:
        log.warn(f"{len(unseeded)} approved/proposed direction(s) not seeded and no supervisor running: "
                 + ", ".join(unseeded))
    summary_rows = {
        "Goal": str(state.get("goal")),
        "Supervisor": "live" if payload["session_live"] else "stopped",
        "Budget": f"{budget['spent_hours']}/{budget['wall_hours']}h, "
                  f"{budget['running']}/{budget['max_concurrent']} slots",
        "Nodes": str(len(payload["nodes"])),
        "Boundary entries": str(len(payload["boundary"])),
    }
    if substance:
        summary_rows["Substance (family ledger)"] = ", ".join(
            f"{grade}: {count}" for grade, count in sorted(substance.items())
        )
    log.key_value(summary_rows)
    rows = []
    for node in payload["nodes"]:
        rows.append((node["node_id"], node.get("kind") or "?", node.get("seeded_from_direction") or "(adopted)"))
    log.results_table(rows or [("none", "-", "no nodes yet")], title="Family nodes")
    pool_rows = []
    for entry in sorted(pool, key=lambda e: (e.get("rank") is None, e.get("rank") or 0)):
        pool_rows.append(
            (entry["direction_id"], entry.get("status", "?"),
             f"rank={entry.get('rank')} kind={entry.get('kind')} {str(entry.get('title') or '')[:60]}")
        )
    log.results_table(pool_rows or [("empty", "-", "run analyze or wait for harvest")], title="Direction pool")


@app.command("propose")
def propose(
    project_path: str = typer.Argument(".", help="Family root project."),
    markdown: str = typer.Argument(..., help="Human-written direction markdown file."),
    rank: int | None = typer.Option(None, "--rank", help="Explicit scheduling rank (1 = first)."),
    kind: str = typer.Option("abstract", "--kind", help="Direction kind: abstract | instantiate."),
    approve: bool = typer.Option(False, "--approve", help="Enter the pool as approved (schedulable immediately)."),
    json_output: bool = typer.Option(False, "--json", help="Print machine-readable JSON."),
) -> None:
    """First-class pool entry for a human-written direction file (no EVOLVE.json hand-editing)."""
    root = _root(project_path)
    # EVOLVE.json is single-writer: a propose landing between a live tick's
    # read_state and write_state would be silently lost.
    if _tmux_session_exists(evolve_session_name(root)):
        raise typer.BadParameter(
            "the evolve supervisor is running and owns EVOLVE.json; "
            "run `iteris evolve stop`, propose, then `iteris evolve run`"
        )
    try:
        entry = propose_direction_state(
            root, markdown=Path(markdown), rank=rank, kind=kind, approve=approve
        )
    except EvolveError as exc:
        raise typer.BadParameter(str(exc)) from exc
    if json_output:
        typer.echo(json.dumps(entry, indent=2, ensure_ascii=False))
        return
    log.success(
        f"proposed: {entry['direction_id']} (status={entry['status']}, rank={entry.get('rank')})"
    )
    if not approve:
        log.info("it will be approved on the supervisor's first tick after restart; or rerun with --approve")


@app.command("veto")
def veto(
    project_path: str = typer.Argument(".", help="Family root project."),
    direction_id: str = typer.Argument(..., help="Direction id to veto."),
    why: str | None = typer.Option(None, "--why", help="Reason for the veto; fed back into analyze/revise prompts to suppress the genre."),
    json_output: bool = typer.Option(False, "--json", help="Print machine-readable JSON."),
) -> None:
    """Human veto: permanently exclude a proposed direction."""
    root = _root(project_path)
    try:
        entry = veto_direction_state(root, direction_id, why=why)
    except EvolveError as exc:
        raise typer.BadParameter(str(exc)) from exc
    if json_output:
        typer.echo(json.dumps(entry, indent=2, ensure_ascii=False))
        return
    log.success(f"vetoed: {direction_id}")


@app.command("report")
def report(
    project_path: str = typer.Argument(".", help="Family root project."),
    json_output: bool = typer.Option(False, "--json", help="Print machine-readable JSON."),
) -> None:
    """Show the rolling supervision report and stage-report inventory."""
    root = _root(project_path)
    rolling = root / ".iteris" / "supervision" / "REPORT.md"
    stage_reports = sorted(str(p.relative_to(root)) for p in (root / "artifacts" / "reports").glob("*/report.md"))
    state = read_state(root)
    payload = {
        "rolling_report": str(rolling) if rolling.exists() else None,
        "stage_reports": stage_reports,
        "boundary": state.get("boundary", []),
        "budget": budget_status(state),
    }
    if json_output:
        typer.echo(json.dumps(payload, indent=2, ensure_ascii=False))
        return
    if rolling.exists():
        typer.echo(rolling.read_text(encoding="utf-8"))
    else:
        log.info("no rolling report yet (supervisor has not run)")
    if stage_reports:
        log.results_table([(p, "ok", "") for p in stage_reports], title="Stage reports (immutable)")


@app.command("stop")
def stop(
    project_path: str = typer.Argument(".", help="Family root project."),
    json_output: bool = typer.Option(False, "--json", help="Print machine-readable JSON."),
) -> None:
    """Stop the master supervisor. Running children stay alive and are
    re-adopted by a later `iteris evolve run`."""
    root = _root(project_path)
    session_name = evolve_session_name(root)
    stopped = False
    if _tmux_session_exists(session_name):
        subprocess.run(["tmux", "kill-session", "-t", session_name], check=False)
        stopped = True
    try:
        unseeded = unseeded_open(read_state(root))
    except EvolveError:
        unseeded = []
    payload = {"session_name": session_name, "stopped": stopped, "unseeded_directions": unseeded}
    if json_output:
        typer.echo(json.dumps(payload, indent=2, ensure_ascii=False))
        return
    if stopped:
        log.success(f"evolve supervisor stopped: {session_name} (children left running)")
    else:
        log.warn(f"evolve supervisor not running: {session_name}")
    if unseeded:
        log.warn(
            f"{len(unseeded)} approved/proposed direction(s) remain unseeded: "
            + ", ".join(unseeded)
        )


def _substance_summary(root: Path) -> dict[str, int]:
    """Family-ledger output counts by substance grade ([NEW]/[STD]/[MAP])."""
    from iteris.memory.family import load_family_index

    counts: dict[str, int] = {}
    for entry in load_family_index(root):
        grade = entry.get("substance")
        if isinstance(grade, str) and grade.strip():
            counts[grade] = counts.get(grade, 0) + 1
    return counts


def _tmux_session_exists(session_name: str) -> bool:
    try:
        proc = subprocess.run(
            ["tmux", "has-session", "-t", session_name], capture_output=True, text=True, timeout=10
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False
    return proc.returncode == 0

"""Subagent launch and inspection commands."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import typer

from iteris import log
from iteris.agents.execute import MODE_GUIDANCE, launch_execute_agent
from iteris.agents.explore import launch_explore_agent
from iteris.agents.runtime import agent_run_summary, latest_agent_run, list_agent_runs, tail_text
from iteris.events import record_event
from iteris.project import read_json, require_project
from iteris.tasks import TASK_POOL_MODES, ensure_task_pool, select_ready_tasks, update_pool_task


app = typer.Typer(help="Launch and inspect Iteris background subagents.")


@app.command("explore")
def explore(
    project_path: str = typer.Argument(".", help="Iteris project path."),
    focus: str = typer.Option("Inspect the active frontier and propose non-obvious next routes.", "--focus", "-f"),
    detached: bool = typer.Option(False, "--detach/--foreground", help="Run as a background worker."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Write request and prompt without launching the executor."),
    executor: str | None = typer.Option(None, "--executor", "-e", help="Agent CLI: codex or claude. Defaults to $ITERIS_EXECUTOR (inherited from the /goal loop), then codex."),
    model: str | None = typer.Option(None, "--model"),
    reasoning_effort: str | None = typer.Option(None, "--reasoning-effort"),
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    root = require_project(project_path)
    result = launch_explore_agent(
        root,
        focus=focus,
        detached=detached,
        dry_run=dry_run,
        executor=executor,
        model=model,
        reasoning_effort=reasoning_effort,
    )
    _print_result(result, json_output=json_output)


@app.command("execute")
def execute(
    project_path: str = typer.Argument(".", help="Iteris project path."),
    task_id: str | None = typer.Option(None, "--task-id", help="TASK_POOL task id. Defaults to the highest-priority ready task."),
    mode: str | None = typer.Option(None, "--mode", help=f"Execution mode: {', '.join(sorted(TASK_POOL_MODES))}."),
    detached: bool = typer.Option(False, "--detach/--foreground", help="Run as a background worker."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Write request and prompt without launching the executor."),
    executor: str | None = typer.Option(None, "--executor", "-e", help="Agent CLI: codex or claude. Defaults to $ITERIS_EXECUTOR (inherited from the /goal loop), then codex."),
    model: str | None = typer.Option(None, "--model"),
    reasoning_effort: str | None = typer.Option(None, "--reasoning-effort"),
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    root = require_project(project_path)
    task = _resolve_task(root, task_id=task_id, mode=mode)
    resolved_mode = mode or str(task.get("mode") or "foundation")
    if resolved_mode not in MODE_GUIDANCE:
        raise typer.BadParameter(f"invalid execution mode: {resolved_mode}")
    result = launch_execute_agent(
        root,
        task=task,
        mode=resolved_mode,
        detached=detached,
        dry_run=dry_run,
        executor=executor,
        model=model,
        reasoning_effort=reasoning_effort,
    )
    if not dry_run:
        status = "running" if detached else ("review" if result.get("status") == "completed" else "blocked")
        update_pool_task(root, str(task["task_id"]), status=status, assigned_agent_run=result["run_id"])
        record_event(root, "task_pool_updated", {"action": "agent_execute", "task_id": task["task_id"], "status": status, "agent_run": result["run_id"]})
    _print_result(result, json_output=json_output)


@app.command("runs")
def runs(
    project_path: str = typer.Argument(".", help="Iteris project path."),
    limit: int = typer.Option(20, "--limit", "-n"),
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    root = require_project(project_path)
    result = list_agent_runs(root, limit=limit)
    if json_output:
        typer.echo(json.dumps(result, indent=2, ensure_ascii=False))
        return
    rows = [(item["run_id"], item.get("role") or "?", item.get("mode") or "-", item.get("status") or "?") for item in result]
    log.results_table(rows or [("none", "skipped", "", "no agent runs")], title="Agent runs")


TERMINAL_AGENT_RUN_STATUSES = {"completed", "failed", "exited_unknown", "dry_run"}


@app.command("wait")
def wait(
    project_path: str = typer.Argument(".", help="Iteris project path."),
    run_id: str | None = typer.Option(None, "--run-id", "-r", help="Run id. Defaults to the latest run."),
    timeout: int = typer.Option(7200, "--timeout", help="Maximum seconds to wait."),
    poll_interval: float = typer.Option(10.0, "--poll-interval", help="Seconds between status checks."),
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    """Block until an agent run reaches a terminal status, then print its summary.

    Replaces hand-rolled `until ...; do sleep ...; done` polling loops. A run
    whose worker pid dies without writing a terminal status resolves as
    `exited_unknown` instead of hanging forever.
    """
    import time as _time

    root = require_project(project_path)
    if run_id and ("/" in run_id or "\\" in run_id or ".." in run_id):
        raise typer.BadParameter(f"invalid run id: {run_id}")
    run_dir = root / "artifacts" / "agent_runs" / run_id if run_id else latest_agent_run(root)
    if run_dir is None or not run_dir.exists():
        raise typer.BadParameter("agent run not found")
    deadline = _time.monotonic() + max(0, timeout)
    poll_interval = max(0.5, poll_interval)
    while True:
        # agent_run_summary self-heals dead-pid runs to exited_unknown,
        # so this loop always terminates once the worker is gone.
        summary = agent_run_summary(root, run_dir)
        status = str(summary.get("status") or "")
        if status in TERMINAL_AGENT_RUN_STATUSES:
            payload = {**summary, "timed_out": False}
            break
        if _time.monotonic() >= deadline:
            payload = {**summary, "timed_out": True}
            break
        _time.sleep(max(0.1, min(poll_interval, deadline - _time.monotonic())))
    if json_output:
        typer.echo(json.dumps(payload, indent=2, ensure_ascii=False))
    else:
        log.key_value(
            {
                "Run": payload["run_id"],
                "Status": str(payload.get("status")),
                "Timed out": str(payload["timed_out"]).lower(),
                "Output": str(payload.get("output_markdown") or "(none)"),
            }
        )
    if payload["timed_out"]:
        raise typer.Exit(124)


@app.command("inspect")
def inspect(
    project_path: str = typer.Argument(".", help="Iteris project path."),
    run_id: str | None = typer.Option(None, "--run-id", "-r", help="Run id. Defaults to the latest run."),
    tail: int = typer.Option(80, "--tail", "-n", help="Log lines to include."),
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    root = require_project(project_path)
    run_dir = root / "artifacts" / "agent_runs" / run_id if run_id else latest_agent_run(root)
    if run_dir is None or not run_dir.exists():
        raise typer.BadParameter("agent run not found")
    summary = agent_run_summary(root, run_dir)
    output_json_path = run_dir / "output.json"
    output_payload = read_json(output_json_path, default=None) if output_json_path.exists() else None
    payload = {
        **summary,
        "status": read_json(run_dir / "status.json", default={}),
        "request": read_json(run_dir / "request.json", default={}),
        "output_json_payload": output_payload,
        "codex_log_tail": tail_text(run_dir / "codex.log", lines=tail),
        "worker_log_tail": tail_text(run_dir / "worker.log", lines=tail),
    }
    if json_output:
        typer.echo(json.dumps(payload, indent=2, ensure_ascii=False))
        return
    log.key_value(
        {
            "Run": summary["run_id"],
            "Role": str(summary.get("role")),
            "Mode": str(summary.get("mode") or "-"),
            "Task": str(summary.get("task_id") or "-"),
            "Status": str(summary.get("status")),
            "Dir": summary["agent_run_dir"],
        }
    )
    if payload["codex_log_tail"]:
        log.header("Codex log tail")
        typer.echo(payload["codex_log_tail"])
    if payload["worker_log_tail"]:
        log.header("Worker log tail")
        typer.echo(payload["worker_log_tail"])


def _resolve_task(root: Path, *, task_id: str | None, mode: str | None) -> dict[str, Any]:
    pool = ensure_task_pool(root)
    if task_id:
        for task in pool.get("tasks", []):
            if isinstance(task, dict) and task.get("task_id") == task_id:
                return task
        raise typer.BadParameter(f"task id not found in TASK_POOL.json: {task_id}")
    ready = select_ready_tasks(root, limit=1, mode=mode)
    if not ready:
        raise typer.BadParameter("no ready TASK_POOL task found")
    return ready[0]


def _print_result(result: dict[str, Any], *, json_output: bool) -> None:
    if json_output:
        typer.echo(json.dumps(result, indent=2, ensure_ascii=False))
        return
    log.key_value(
        {
            "Run": result["run_id"],
            "Role": str(result.get("role")),
            "Mode": str(result.get("mode") or "-"),
            "Task": str(result.get("task_id") or "-"),
            "Status": str(result.get("status")),
            "Dir": result["agent_run_dir"],
            "Prompt": result["prompt_path"],
            "Log": result["codex_log"],
        }
    )

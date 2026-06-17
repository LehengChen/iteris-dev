"""Task board commands."""

from __future__ import annotations

import json

import typer

from iteris.events import record_event
from iteris import log
from iteris.messages import unread_summary
from iteris.project import require_project
from iteris.tasks import (
    TASK_POOL_MODES,
    TASK_POOL_STATUSES,
    add_task,
    ensure_task_pool,
    list_tasks,
    normalize_task_status,
    select_ready_tasks,
    update_pool_task,
    upsert_pool_task,
    validate_task_pool,
)

app = typer.Typer(help="Inspect and update the task board.")
pool_app = typer.Typer(help="Inspect and update TASK_POOL.json.")


@app.command("add")
def add(
    project_path: str = typer.Argument(".", help="Iteris project path."),
    title: str = typer.Option(..., "--title", "-t"),
    category: str = typer.Option("foundation", "--category", "-c"),
    objective: str = typer.Option(..., "--objective", "-o"),
    claim_ceiling: str = typer.Option("submitted", "--claim-ceiling"),
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    root = require_project(project_path)
    task = add_task(root, title=title, category=category, objective=objective, claim_ceiling=claim_ceiling)
    record_event(root, "task_added", {"task_id": task["task_id"], "category": category})
    if json_output:
        typer.echo(json.dumps(task, indent=2, ensure_ascii=False))
        return
    log.success(f"Added {task['task_id']}")


@app.command("list")
def list_(
    project_path: str = typer.Argument(".", help="Iteris project path."),
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    root = require_project(project_path)
    tasks = list_tasks(root)
    if json_output:
        typer.echo(json.dumps(tasks, indent=2, ensure_ascii=False))
        return
    rows = [(task["task_id"], task.get("status", "?"), task.get("objective", "")[:220]) for task in tasks]
    log.results_table(rows or [("none", "skipped", "no tasks")], title="Tasks")


@pool_app.command("show")
def pool_show(
    project_path: str = typer.Argument(".", help="Iteris project path."),
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    root = require_project(project_path)
    pool = ensure_task_pool(root)
    if json_output:
        payload = dict(pool)
        unread = unread_summary(root)
        if unread:
            payload["unread_messages"] = unread
        typer.echo(json.dumps(payload, indent=2, ensure_ascii=False))
        return
    rows = [
        (task.get("task_id", "?"), task.get("mode", "?"), task.get("status", "?"), str(task.get("objective", ""))[:180])
        for task in pool.get("tasks", [])
        if isinstance(task, dict)
    ]
    log.results_table(rows or [("none", "skipped", "no pool tasks", "")], title="TASK_POOL.json")


@pool_app.command("validate")
def pool_validate(
    project_path: str = typer.Argument(".", help="Iteris project path."),
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    root = require_project(project_path)
    result = validate_task_pool(root)
    if json_output:
        typer.echo(json.dumps(result, indent=2, ensure_ascii=False))
        return
    if result["ok"]:
        log.success(f"TASK_POOL valid: {result['task_count']} tasks")
    else:
        log.warn(f"TASK_POOL invalid: {len(result['errors'])} errors")
    rows = [(item["location"], item["issue"], "") for item in [*result["errors"], *result["warnings"]]]
    log.results_table(rows or [("ok", "no errors", "")], title="Task pool validation")


@pool_app.command("add")
def pool_add(
    project_path: str = typer.Argument(".", help="Iteris project path."),
    task_id: str = typer.Option(..., "--task-id", help="Stable task id."),
    mode: str = typer.Option("foundation", "--mode", help=f"Task mode: {', '.join(sorted(TASK_POOL_MODES))}."),
    objective: str = typer.Option(..., "--objective", "-o", help="Task objective."),
    priority: int = typer.Option(0, "--priority", "-p", help="Higher priority is selected first."),
    status: str = typer.Option("ready", "--status", help=f"Task status: {', '.join(sorted(TASK_POOL_STATUSES))}."),
    dependency: list[str] = typer.Option([], "--dependency", help="Task id dependency. Repeatable."),
    input_path: list[str] = typer.Option([], "--input", help="Input artifact path. Repeatable."),
    expected_output: list[str] = typer.Option([], "--expected-output", help="Expected output artifact path. Repeatable."),
    note: list[str] = typer.Option([], "--note", help="Task note. Repeatable."),
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    root = require_project(project_path)
    task = upsert_pool_task(
        root,
        task_id=task_id,
        mode=mode,
        objective=objective,
        status=status,
        priority=priority,
        dependencies=dependency,
        inputs=input_path,
        expected_outputs=expected_output,
        notes=note,
    )
    record_event(root, "task_pool_updated", {"action": "upsert", "task_id": task_id, "mode": mode, "status": status})
    if json_output:
        typer.echo(json.dumps(task, indent=2, ensure_ascii=False))
        return
    log.success(f"Upserted {task_id}")


@pool_app.command("update")
def pool_update(
    project_path: str = typer.Argument(".", help="Iteris project path."),
    task_id: str = typer.Option(..., "--task-id", help="Task id to update."),
    status: str | None = typer.Option(None, "--status", help=f"Task status: {', '.join(sorted(TASK_POOL_STATUSES))}."),
    priority: int | None = typer.Option(None, "--priority", "-p"),
    assigned_agent_run: str | None = typer.Option(None, "--assigned-agent-run"),
    note: list[str] = typer.Option([], "--note", help="Append notes. Repeatable."),
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    root = require_project(project_path)
    if status is not None:
        status = normalize_task_status(status)
    if status is not None and status not in TASK_POOL_STATUSES:
        raise typer.BadParameter(f"invalid task status: {status}")
    task = update_pool_task(root, task_id, status=status, priority=priority, assigned_agent_run=assigned_agent_run, append_notes=note)
    record_event(root, "task_pool_updated", {"action": "update", "task_id": task_id, "status": task.get("status")})
    if json_output:
        typer.echo(json.dumps(task, indent=2, ensure_ascii=False))
        return
    log.success(f"Updated {task_id}")


@pool_app.command("select-ready")
def pool_select_ready(
    project_path: str = typer.Argument(".", help="Iteris project path."),
    mode: str | None = typer.Option(None, "--mode", help="Optional mode filter."),
    limit: int = typer.Option(5, "--limit", "-n"),
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    root = require_project(project_path)
    tasks = select_ready_tasks(root, limit=limit, mode=mode)
    if json_output:
        typer.echo(json.dumps(tasks, indent=2, ensure_ascii=False))
        return
    rows = [(task.get("task_id", "?"), task.get("mode", "?"), str(task.get("priority", 0)), str(task.get("objective", ""))[:180]) for task in tasks]
    log.results_table(rows or [("none", "skipped", "", "no ready tasks")], title="Ready tasks")


app.add_typer(pool_app, name="pool")

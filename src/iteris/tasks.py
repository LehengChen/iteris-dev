"""Task board and global task pool helpers."""

from __future__ import annotations

import json
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from iteris.project import append_jsonl, now_iso, read_json, slugify, write_json

try:
    import fcntl
except ImportError:  # pragma: no cover - Iteris targets macOS/Linux.
    fcntl = None  # type: ignore[assignment]


TASK_POOL_SCHEMA_VERSION = "iteris.task_pool.v0"
TASK_POOL_MODES = {"foundation", "proof", "experiment", "algorithm"}
TASK_POOL_STATUSES = {"ready", "running", "review", "blocked", "done", "rejected", "paused"}
TASK_POOL_STATUS_ALIASES = {"completed": "done", "complete": "done"}


def task_pool_path(project_root: Path) -> Path:
    return project_root / "tasks" / "TASK_POOL.json"


def normalize_task_status(status: str) -> str:
    return TASK_POOL_STATUS_ALIASES.get(status, status)


def default_task_pool(project_root: Path) -> dict[str, Any]:
    return {
        "schema_version": TASK_POOL_SCHEMA_VERSION,
        "project_id": slugify(project_root.resolve().name, 60),
        "updated_at": now_iso(),
        "active_frontier": "",
        "tasks": [],
    }


def ensure_task_pool(project_root: Path) -> dict[str, Any]:
    path = task_pool_path(project_root)
    with _task_pool_lock(project_root):
        if not path.exists():
            pool = default_task_pool(project_root)
            _save_task_pool_unlocked(project_root, pool)
            return pool
        return load_task_pool(project_root)


def load_task_pool(project_root: Path) -> dict[str, Any]:
    payload = read_json(task_pool_path(project_root), default=None)
    if not isinstance(payload, dict):
        payload = default_task_pool(project_root)
    payload.setdefault("schema_version", TASK_POOL_SCHEMA_VERSION)
    payload.setdefault("project_id", slugify(project_root.resolve().name, 60))
    payload.setdefault("updated_at", now_iso())
    payload.setdefault("active_frontier", "")
    payload.setdefault("tasks", [])
    if not isinstance(payload["tasks"], list):
        payload["tasks"] = []
    return payload


def save_task_pool(project_root: Path, pool: dict[str, Any]) -> dict[str, Any]:
    with _task_pool_lock(project_root):
        return _save_task_pool_unlocked(project_root, pool)


def _save_task_pool_unlocked(project_root: Path, pool: dict[str, Any]) -> dict[str, Any]:
    pool = dict(pool)
    pool["schema_version"] = TASK_POOL_SCHEMA_VERSION
    pool["updated_at"] = now_iso()
    write_json(task_pool_path(project_root), pool)
    return pool


def validate_task_pool(project_root: Path) -> dict[str, Any]:
    pool = load_task_pool(project_root)
    errors: list[dict[str, str]] = []
    warnings: list[dict[str, str]] = []
    seen: set[str] = set()
    statuses: dict[str, str] = {}
    for index, task in enumerate(pool.get("tasks", [])):
        if not isinstance(task, dict):
            errors.append({"location": f"tasks[{index}]", "issue": "task entry must be an object"})
            continue
        task_id = str(task.get("task_id") or "")
        if not task_id:
            errors.append({"location": f"tasks[{index}]", "issue": "task_id is required"})
            continue
        if task_id in seen:
            errors.append({"location": task_id, "issue": "duplicate task_id"})
        seen.add(task_id)
        statuses[task_id] = str(task.get("status") or "")
        mode = str(task.get("mode") or "")
        status = normalize_task_status(str(task.get("status") or ""))
        if status != task.get("status"):
            task["status"] = status
        if mode not in TASK_POOL_MODES:
            errors.append({"location": task_id, "issue": f"invalid mode: {mode}"})
        if status not in TASK_POOL_STATUSES:
            errors.append({"location": task_id, "issue": f"invalid status: {status}"})
        if not str(task.get("objective") or "").strip():
            errors.append({"location": task_id, "issue": "objective is required"})
        for dep in task.get("dependencies") or []:
            if dep not in seen and not any(isinstance(t, dict) and t.get("task_id") == dep for t in pool.get("tasks", [])):
                warnings.append({"location": task_id, "issue": f"dependency not found in pool: {dep}"})
    return {
        "schema_version": "iteris.task_pool_validation.v0",
        "ok": not errors,
        "errors": errors,
        "warnings": warnings,
        "task_count": len([task for task in pool.get("tasks", []) if isinstance(task, dict)]),
        "status_counts": {status: list(statuses.values()).count(status) for status in sorted(set(statuses.values())) if status},
        "path": str(task_pool_path(project_root)),
    }


def upsert_pool_task(
    project_root: Path,
    *,
    task_id: str,
    mode: str,
    objective: str,
    status: str = "ready",
    priority: int = 0,
    dependencies: list[str] | None = None,
    inputs: list[str] | None = None,
    expected_outputs: list[str] | None = None,
    assigned_agent_run: str | None = None,
    notes: list[str] | None = None,
) -> dict[str, Any]:
    if mode not in TASK_POOL_MODES:
        raise ValueError(f"invalid task mode: {mode}")
    status = normalize_task_status(status)
    if status not in TASK_POOL_STATUSES:
        raise ValueError(f"invalid task status: {status}")
    with _task_pool_lock(project_root):
        pool = load_task_pool(project_root)
        now = now_iso()
        task = {
            "schema_version": "iteris.task_pool_task.v0",
            "task_id": task_id,
            "mode": mode,
            "objective": objective,
            "status": status,
            "priority": int(priority),
            "dependencies": dependencies or [],
            "inputs": inputs or [],
            "expected_outputs": expected_outputs or [],
            "assigned_agent_run": assigned_agent_run,
            "notes": notes or [],
            "created_at": now,
            "updated_at": now,
        }
        replaced = False
        tasks = []
        for existing in pool.get("tasks", []):
            if isinstance(existing, dict) and existing.get("task_id") == task_id:
                merged = {**existing, **{key: value for key, value in task.items() if value not in (None, [], "")}}
                merged["updated_at"] = now
                tasks.append(merged)
                task = merged
                replaced = True
            else:
                tasks.append(existing)
        if not replaced:
            tasks.append(task)
        pool["tasks"] = tasks
        _save_task_pool_unlocked(project_root, pool)
        return task


def update_pool_task(project_root: Path, task_id: str, **updates: Any) -> dict[str, Any]:
    with _task_pool_lock(project_root):
        pool = load_task_pool(project_root)
        now = now_iso()
        append_notes = updates.pop("append_notes", None)
        for task in pool.get("tasks", []):
            if isinstance(task, dict) and task.get("task_id") == task_id:
                for key, value in updates.items():
                    if value is not None:
                        if key == "status":
                            value = normalize_task_status(str(value))
                        task[key] = value
                if append_notes:
                    existing_notes = list(task.get("notes") or [])
                    existing_notes.extend(str(note) for note in append_notes if str(note))
                    task["notes"] = existing_notes
                task["updated_at"] = now
                _save_task_pool_unlocked(project_root, pool)
                return task
    raise KeyError(f"task not found in TASK_POOL.json: {task_id}")


def select_ready_tasks(project_root: Path, *, limit: int = 5, mode: str | None = None) -> list[dict[str, Any]]:
    pool = load_task_pool(project_root)
    by_id = {str(task.get("task_id")): task for task in pool.get("tasks", []) if isinstance(task, dict)}
    ready: list[dict[str, Any]] = []
    for task in by_id.values():
        if task.get("status") != "ready":
            continue
        if mode and task.get("mode") != mode:
            continue
        deps = [str(dep) for dep in task.get("dependencies") or []]
        if any(by_id.get(dep, {}).get("status") not in {"done", "review"} for dep in deps):
            continue
        ready.append(task)
    ready.sort(key=lambda item: (-int(item.get("priority") or 0), str(item.get("created_at") or ""), str(item.get("task_id") or "")))
    return ready[:limit]


def add_task(
    project_root: Path,
    *,
    title: str,
    category: str,
    objective: str,
    claim_ceiling: str = "submitted",
    status: str = "open",
    verification_required: bool = True,
) -> dict[str, Any]:
    task_id = f"task-{slugify(title)}"
    task = {
        "schema_version": "iteris.task.v0",
        "task_id": task_id,
        "title": title,
        "category": category,
        "objective": objective,
        "claim_ceiling": claim_ceiling,
        "status": status,
        "verification_required": verification_required,
        "created_at": now_iso(),
    }
    write_json(project_root / "tasks" / f"{task_id}.json", task)
    append_jsonl(project_root / "tasks" / "TASK_BOARD.jsonl", task)
    upsert_pool_task(
        project_root,
        task_id=task_id,
        mode=category if category in TASK_POOL_MODES else "foundation",
        objective=objective,
        status="ready" if status == "open" else status if status in TASK_POOL_STATUSES else "ready",
        priority=0,
        notes=[f"Mirrored from legacy task board category `{category}`."],
    )
    return task


def list_tasks(project_root: Path) -> list[dict[str, Any]]:
    tasks = []
    for path in sorted((project_root / "tasks").glob("task-*.json")):
        try:
            tasks.append(json.loads(path.read_text(encoding="utf-8")))
        except Exception:
            continue
    return tasks


def repair_orphaned_task(
    project_root: Path,
    task_id: str,
    *,
    to_status: str,
    note: str,
    expected_assigned_run: str | None,
    clear_assigned_run: bool = False,
) -> dict[str, Any] | None:
    """Repair a task flagged by a liveness scan, revalidating under the pool lock.

    The scan happens outside the lock, so the task may legitimately change in
    between (a worker finishing and harvesting it, for example). The repair is
    applied only if the task is still ``running`` with the same assigned run
    the scan saw; otherwise ``None`` is returned and the caller records a skip.

    ``update_pool_task`` deliberately ignores ``None`` values, so clearing
    ``assigned_agent_run`` needs this dedicated helper.
    """
    to_status = normalize_task_status(to_status)
    if to_status not in TASK_POOL_STATUSES:
        raise ValueError(f"invalid task status: {to_status}")
    with _task_pool_lock(project_root):
        pool = load_task_pool(project_root)
        for task in pool.get("tasks", []):
            if not isinstance(task, dict) or task.get("task_id") != task_id:
                continue
            current_assigned = task.get("assigned_agent_run") or None
            if task.get("status") != "running" or current_assigned != (expected_assigned_run or None):
                return None
            task["status"] = to_status
            if clear_assigned_run:
                task["assigned_agent_run"] = None
            notes = list(task.get("notes") or [])
            notes.append(note)
            task["notes"] = notes
            task["updated_at"] = now_iso()
            _save_task_pool_unlocked(project_root, pool)
            return task
    raise KeyError(f"task not found in TASK_POOL.json: {task_id}")


STALE_TASK_HOURS = {"running": 2.0, "review": 2.0}


def stale_status_tasks(
    pool: dict[str, Any],
    *,
    now: str | None = None,
    thresholds: dict[str, float] | None = None,
) -> list[dict[str, Any]]:
    """Tasks sitting in an active status (running/review) past a freshness threshold.

    Pure function over a loaded pool so callers and tests control the clock.
    Tasks with missing or unparseable timestamps are skipped rather than flagged.
    """
    thresholds = thresholds or STALE_TASK_HOURS
    reference = _parse_iso(now or now_iso())
    if reference is None:
        return []
    stale: list[dict[str, Any]] = []
    for task in pool.get("tasks", []):
        if not isinstance(task, dict):
            continue
        status = str(task.get("status") or "")
        limit = thresholds.get(status)
        if limit is None:
            continue
        updated = _parse_iso(str(task.get("updated_at") or ""))
        if updated is None:
            continue
        hours = (reference - updated).total_seconds() / 3600.0
        if hours >= limit:
            stale.append(
                {
                    "task_id": task.get("task_id"),
                    "status": status,
                    "hours_in_status": round(hours, 1),
                    "assigned_agent_run": task.get("assigned_agent_run"),
                }
            )
    stale.sort(key=lambda item: -item["hours_in_status"])
    return stale


def _parse_iso(value: str):
    from datetime import datetime, timezone

    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


@contextmanager
def _task_pool_lock(project_root: Path):
    lock_path = project_root / ".iteris" / "locks" / "TASK_POOL.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+", encoding="utf-8") as handle:
        if fcntl is not None:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            if fcntl is not None:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

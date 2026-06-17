"""Dashboard data-contract commands (`iteris tool ui ...`).

These commands are the contract consumed by the Node dashboard server. They
keep all discovery / live-detection / structured-log normalization in Python so
the Node layer stays a thin file-watcher + static host.
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import typer

from iteris.log_adapters import (
    LOG_ADAPTER_CLAUDE,
    LOG_ADAPTER_CODEX,
    adapter_for_executor,
    infer_log_adapter,
    normalize_structured_file,
    resolve_log_adapter,
)
from iteris.project import read_json, require_project

app = typer.Typer(help="Dashboard data contract (consumed by `iteris dashboard`).")

# Evolve/supervision data-contract commands live in their own module.
from iteris.commands.ui_evolve import register as _register_evolve  # noqa: E402

_register_evolve(app)

def _is_structured(rel: str) -> bool:
    return rel.endswith(".jsonl")


@app.command()
def streams(
    project_path: str = typer.Argument(".", help="Iteris project path."),
    json_output: bool = typer.Option(False, "--json", help="Print machine-readable JSON."),
) -> None:
    """List every observable stream: main loop pane + sub-agent/verifier runs."""
    root = require_project(project_path)
    result: list[dict[str, Any]] = []

    # Lazy imports avoid import cycles at module load.
    from iteris.commands.goal import _mtime_or_zero, latest_goal_logs, tmux_session_exists
    from iteris.commands.workflow import current_run_state, default_session_name

    # 1) tmux pane.logs are an implementation detail: keep reading their sibling
    # meta.json files for live detection and labels, but do not expose the raw
    # ANSI pane streams in the dashboard.
    run_state = current_run_state(root)
    session_name = run_state.get("session_name") or default_session_name(root)
    goal_logs = latest_goal_logs(root, session_name)
    live = False
    try:
        live = tmux_session_exists(session_name)
    except Exception:
        live = False

    def _session_live(name: str | None) -> bool:
        if not name:
            return False
        try:
            return tmux_session_exists(name)
        except Exception:
            return False

    # run_name (goal-<slug>-<stamp>) → (label, live, meta); shared with the
    # structured-log scan below, whose per-run agent-home dirs carry the same
    # names.
    run_info: dict[str, tuple[str, bool]] = {}
    run_meta: dict[str, dict[str, Any]] = {}
    logs_dir = root / ".iteris" / "logs"
    if logs_dir.is_dir():
        pane_logs = sorted(logs_dir.glob("goal-*.pane.log"), key=_mtime_or_zero, reverse=True)
        for pane in pane_logs[:5]:
            run_name = pane.name[: -len(".pane.log")]
            pane_meta_path = pane.with_name(run_name + ".meta.json")
            pane_meta = read_json(pane_meta_path, default={}) if pane_meta_path.exists() else {}
            if isinstance(pane_meta, dict):
                run_meta[run_name] = pane_meta
            pane_session = pane_meta.get("session_name") if isinstance(pane_meta, dict) else None
            pane_live = live if pane_session == session_name else _session_live(pane_session)
            label = pane_session or run_name
            run_info[run_name] = (label, pane_live)

    # 1b) Main /goal loop, structured → per-run executor-home JSONL.
    # Codex writes sessions/**/rollout-*.jsonl; Claude Code writes
    # projects/**/*.jsonl. Both live under .iteris/codex_home/<run>/ for
    # historical compatibility, and the log adapter hides the raw layout.
    meta_path = goal_logs.get("meta")
    meta = read_json(Path(meta_path), default={}) if meta_path else {}
    codex_home_rel = meta.get("codex_home") if isinstance(meta, dict) else None
    current_home = (root / codex_home_rel).resolve() if codex_home_rel else None
    codex_home_root = root / ".iteris" / "codex_home"
    if codex_home_root.is_dir():
        run_dirs = sorted(
            (p for p in codex_home_root.iterdir() if p.is_dir() and p.name.startswith("goal-")),
            key=_mtime_or_zero,
            reverse=True,
        )
        for run_dir in run_dirs[:5]:
            pane_meta = run_meta.get(run_dir.name, {})
            executor = str(pane_meta.get("executor") or "")
            if not executor:
                executor = LOG_ADAPTER_CLAUDE if (run_dir / "projects").is_dir() else LOG_ADAPTER_CODEX
            adapter = adapter_for_executor(executor)
            structured_logs = adapter.find_logs(run_dir)
            if not structured_logs:
                continue
            structured_log = structured_logs[0]
            is_current = current_home is not None and run_dir.resolve() == current_home
            label, run_live = run_info.get(run_dir.name, (run_dir.name, False))
            if is_current:
                run_live = live
                title = f"{session_name} (goal loop · {adapter.label})"
            else:
                title = f"{label} ({adapter.label})"
            result.append(
                {
                    "id": f"rollout:{adapter.name}:{run_dir.name}",
                    "kind": "pane",
                    "title": title,
                    "path": str(structured_log.relative_to(root)),
                    "live": run_live,
                    "status": "running" if run_live else "idle",
                    "format": "structured",
                    "adapter": adapter.name,
                    "executor": executor,
                    "model": pane_meta.get("model"),
                }
            )

    # 2) Sub-agents → artifacts/agent_runs/<id>/codex.events.jsonl
    from iteris.agents.runtime import list_agent_runs

    for run in list_agent_runs(root, limit=50):
        events = run.get("codex_events")
        if not events or not (root / events).exists():
            continue
        # The events file name is codex.events.jsonl for both executors; the
        # run's recorded executor selects which normalizer decodes it.
        executor = run.get("executor") or LOG_ADAPTER_CODEX
        adapter = adapter_for_executor(executor)
        result.append(
            {
                "id": run["run_id"],
                "kind": "agent",
                "title": f"{run.get('role') or 'agent'}: {run.get('task_id') or run.get('focus') or run['run_id']}",
                "path": events,
                "live": run.get("status") == "running",
                "status": run.get("status"),
                "role": run.get("role"),
                "mode": run.get("mode"),
                "task_id": run.get("task_id"),
                "started_at": run.get("created_at"),
                "format": "structured",
                "adapter": adapter.name,
                "executor": executor,
            }
        )

    # 3) Verifiers → verification/agent_runs/<id>/codex.events.jsonl
    verify_dir = root / "verification" / "agent_runs"
    if verify_dir.exists():
        run_dirs = sorted(
            (p for p in verify_dir.iterdir() if p.is_dir()),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        for run_dir in run_dirs[:50]:
            events = run_dir / "codex.events.jsonl"
            if not events.exists():
                continue
            done = (run_dir / "verification.json").exists()
            request = read_json(run_dir / "request.json", default={})
            executor = (request.get("executor") if isinstance(request, dict) else None) or LOG_ADAPTER_CODEX
            adapter = adapter_for_executor(executor)
            result.append(
                {
                    "id": run_dir.name,
                    "kind": "verify",
                    "title": run_dir.name,
                    "path": str(events.relative_to(root)),
                    "live": not done,
                    "status": "done" if done else "running",
                    "format": "structured",
                    "adapter": adapter.name,
                    "executor": executor,
                }
            )

    typer.echo(json.dumps(result, indent=2 if json_output else None, ensure_ascii=False))


@app.command()
def snapshot(
    relpath: str = typer.Argument(..., help="Log path relative to the project root."),
    project_path: str = typer.Argument(".", help="Iteris project path."),
    json_output: bool = typer.Option(False, "--json", help="Print machine-readable JSON."),
    max_bytes: int = typer.Option(
        0, "--max-bytes", help="Process complete lines within the first N bytes (0 = whole file)."
    ),
    tail_entries: int = typer.Option(
        0, "--tail-entries", help="Keep only the last N normalized entries (0 = all)."
    ),
    adapter: str | None = typer.Option(None, "--adapter", help="Structured log adapter: codex or claude."),
) -> None:
    """One-shot snapshot for initial load: normalized structured log entries."""
    root = require_project(project_path)
    normalized = (root / relpath).resolve()
    if root not in normalized.parents and normalized != root:
        raise typer.BadParameter("path escapes project root")
    if not normalized.exists():
        raise typer.BadParameter(f"not found: {relpath}")
    if not _is_structured(relpath):
        raise typer.BadParameter(f"not a structured (.jsonl) log: {relpath}")

    adapter_name = adapter or infer_log_adapter(relpath)
    try:
        log_adapter = resolve_log_adapter(adapter_name)
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from None
    entries = normalize_structured_file(normalized, adapter=log_adapter.name, max_bytes=max_bytes or None)
    truncated = len(entries) > tail_entries > 0
    if truncated:
        entries = entries[-tail_entries:]
    payload: dict[str, Any] = {
        "format": "structured",
        "adapter": log_adapter.name,
        "entries": entries,
        "truncated": truncated,
    }
    typer.echo(json.dumps(payload, ensure_ascii=False))


def _mtime_iso(path: Path) -> str:
    return datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc).isoformat().replace("+00:00", "Z")


def _iter_verification_records(root: Path):
    """Yield parsed records from VERIFICATION_INDEX.jsonl, skipping bad lines.

    A record is a *result* iff `rec.get("verdict") is not None`; otherwise it
    is a pending request. Shared by `_latest_answer` and `activity`.
    """
    vindex = root / "verification" / "VERIFICATION_INDEX.jsonl"
    if not vindex.exists():
        return
    for line in vindex.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            yield json.loads(line)
        except json.JSONDecodeError:
            continue


def _latest_answer(root: Path) -> dict[str, Any] | None:
    """Terminal-answer descriptor from the newest passing assembly verification.

    The assembly verifier audits exactly which durable facts the final answer
    cites (`checked_fact_ids`), so that list — not a re-parse of the answer
    markdown — is the dependency set the dashboard draws.
    """
    assembly: dict[str, Any] | None = None
    goal: dict[str, Any] | None = None
    for rec in _iter_verification_records(root):
        if rec.get("verdict") is None:
            continue  # request, not result
        ts = str(rec.get("created_at") or "")
        if rec.get("mode") == "assembly" and rec.get("passed"):
            if assembly is None or ts > str(assembly.get("created_at") or ""):
                assembly = rec
        elif rec.get("mode") == "goal_success":
            if goal is None or ts > str(goal.get("created_at") or ""):
                goal = rec
    if assembly is None:
        return None
    fact_ids = [f for f in assembly.get("checked_fact_ids") or [] if isinstance(f, str) and f.startswith("fact:")]
    if not fact_ids:
        return None
    return {
        "target_artifact": assembly.get("target_artifact"),
        "fact_ids": fact_ids,
        "summary": assembly.get("summary") or assembly.get("claim") or "",
        "created_at": assembly.get("created_at"),
        "goal_passed": bool(goal.get("passed")) if goal is not None else None,
    }


def _fact_row(path: Path, root: Path, include_body: bool) -> dict[str, Any]:
    """One fact file → dashboard row. Shared by `facts` (list) and `fact` (detail).

    `body` is the bulk of the polling payload, so the list omits it by default
    and the detail endpoint always includes it.
    """
    from iteris.memory.facts import parse_frontmatter

    try:
        meta, body = parse_frontmatter(path.read_text(encoding="utf-8", errors="replace"))
    except Exception as exc:
        return {"path": str(path.relative_to(root)), "error": str(exc)}
    predecessors = meta.get("predecessors")
    row = {
        "fact_id": meta.get("fact_id"),
        "status": meta.get("status"),
        "fact_type": meta.get("fact_type"),
        "review_level": meta.get("review_level"),
        "claim_policy": meta.get("claim_policy"),
        "claim_summary": meta.get("claim_summary"),
        "source_project": meta.get("source_project"),
        "source_task": meta.get("source_task"),
        "predecessors": predecessors if isinstance(predecessors, list) else [],
        "verification": meta.get("verification"),
        "path": str(path.relative_to(root)),
        "updated_at": _mtime_iso(path),
    }
    if include_body:
        row["body"] = body.strip()
    return row


@app.command()
def facts(
    project_path: str = typer.Argument(".", help="Iteris project path."),
    json_output: bool = typer.Option(False, "--json", help="Print machine-readable JSON."),
    include_body: bool = typer.Option(
        False, "--include-body", help="Include each fact's markdown body (large; off by default)."
    ),
) -> None:
    """Fact-graph payload: every durable fact's metadata and file mtime.

    Reads the fact files directly (not FACT_INDEX.jsonl) because the index
    stamps every row with the rebuild time — per-fact mtimes are what the
    dashboard uses for freshness highlighting. Bodies are omitted unless
    --include-body is passed; fetch a single body via `tool ui fact`.
    """
    from iteris.memory.facts import fact_files

    root = require_project(project_path)
    rows = [_fact_row(path, root, include_body=include_body) for path in fact_files(root)]
    payload: dict[str, Any] = {"schema_version": "iteris.ui_facts.v0", "facts": rows}
    answer = _latest_answer(root)
    if answer is not None:
        payload["answer"] = answer
    typer.echo(json.dumps(payload, indent=2 if json_output else None, ensure_ascii=False))


@app.command()
def fact(
    project_path: str = typer.Argument(".", help="Iteris project path."),
    fact_id: str = typer.Option(..., "--fact-id", help="Durable fact id (fact:...)."),
    json_output: bool = typer.Option(False, "--json", help="Print machine-readable JSON."),
) -> None:
    """Single-fact detail (full row including body); `fact` is null if not found."""
    from iteris.memory.facts import fact_files, parse_frontmatter

    root = require_project(project_path)
    found: dict[str, Any] | None = None
    for path in fact_files(root):
        try:
            meta, _body = parse_frontmatter(path.read_text(encoding="utf-8", errors="replace"))
        except Exception:
            continue
        if meta.get("fact_id") == fact_id:
            found = _fact_row(path, root, include_body=True)
            break
    payload = {"schema_version": "iteris.ui_fact.v0", "fact": found}
    typer.echo(json.dumps(payload, indent=2 if json_output else None, ensure_ascii=False))


@app.command()
def activity(
    project_path: str = typer.Argument(".", help="Iteris project path."),
    json_output: bool = typer.Option(False, "--json", help="Print machine-readable JSON."),
    limit: int = typer.Option(50, "--limit", help="Maximum number of feed items."),
) -> None:
    """Recent-activity feed, newest first: verification outcomes + fact updates.

    Verification requests are only surfaced while no result with the same
    request_id exists yet (i.e. the verification is still pending).
    """
    from iteris.memory.facts import fact_files, parse_frontmatter

    root = require_project(project_path)
    items: list[dict[str, Any]] = []

    results_seen: set[str] = set()
    requests: list[dict[str, Any]] = []
    for rec in _iter_verification_records(root):
        entry = {
            "ts": rec.get("created_at"),
            "id": rec.get("request_id"),
            "mode": rec.get("mode"),
            "title": rec.get("summary") or rec.get("claim") or "",
        }
        if rec.get("verdict") is not None:
            results_seen.add(str(rec.get("request_id")))
            entry.update(type="verification_result", verdict=rec.get("verdict"), passed=rec.get("passed"))
            items.append(entry)
        else:
            entry.update(type="verification_pending")
            requests.append(entry)
    items.extend(req for req in requests if str(req.get("id")) not in results_seen)

    for path in fact_files(root):
        try:
            meta, _body = parse_frontmatter(path.read_text(encoding="utf-8", errors="replace"))
        except Exception:
            continue
        items.append(
            {
                "ts": _mtime_iso(path),
                "type": "fact",
                "id": meta.get("fact_id"),
                "title": meta.get("claim_summary") or "",
                "status": meta.get("status"),
            }
        )

    items = [item for item in items if item.get("ts")]
    items.sort(key=lambda item: str(item["ts"]), reverse=True)
    payload = {"schema_version": "iteris.ui_activity.v0", "items": items[:limit]}
    typer.echo(json.dumps(payload, indent=2 if json_output else None, ensure_ascii=False))


@app.command()
def normalize(
    stream: bool = typer.Option(False, "--stream", help="Read raw structured JSONL from stdin, emit LogEntry JSON lines."),
    adapter: str | None = typer.Option(LOG_ADAPTER_CODEX, "--adapter", help="Structured log adapter: codex or claude."),
) -> None:
    """Long-lived normalizer: stdin raw structured events → stdout unified LogEntry lines."""
    if not stream:
        raise typer.BadParameter("only --stream mode is supported")
    try:
        normalizer = resolve_log_adapter(adapter).normalizer_factory()
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from None
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        mapped = normalizer.feed(event)
        if mapped is None:
            continue
        entries = mapped if isinstance(mapped, list) else [mapped]
        for entry in entries:
            sys.stdout.write(json.dumps(entry, ensure_ascii=False) + "\n")
        sys.stdout.flush()

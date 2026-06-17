"""Agent-oriented project context command."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import typer

from iteris import log
from iteris.artifacts import artifact_layout_summary
from iteris.frontier import frontier_health, frontier_summary, load_frontier_index
from iteris.gitops import status as git_status
from iteris.memory.facts import keystone_facts, validate_project_facts
from iteris.memory.scratch import read_events
from iteris.memory.search import search_memory
from iteris.messages import unread_summary
from iteris.project import read_json, require_project, source_file
from iteris.tasks import list_tasks, load_task_pool, stale_status_tasks
from iteris.verification.local import latest_results, rejection_streaks, stale_verification_requests


def keystone_verification_counts(verifications: list[dict[str, Any]]) -> dict[str, int]:
    """Passed-verification depth per fact, for keystone under-verification.

    Credit a verification only toward the fact it actually targeted
    (``primary_fact_ids`` = the request's explicit ``--fact-id`` set), NOT every
    id that leaked into ``checked_fact_ids`` as a predecessor/context bundle.
    Bundle-crediting silently pushed high in-degree keystones to ``passed >= 2``,
    disabling the panel-reverification safety net for the most load-bearing facts.

    Only agent/panel fact verifications count (structural prechecks and incidental
    mentions don't make a keystone safer); panel seats roll up into their
    aggregate, weighted by seat count. Results without ``primary_fact_ids``
    (legacy) fall back to the old ``checked_fact_ids`` behavior.
    """
    counts: dict[str, int] = {}
    for item in verifications:
        if not item.get("passed"):
            continue
        if str(item.get("mode") or "") != "fact":
            continue
        scope = str(item.get("verification_scope") or "")
        # Single-agent verifications carry an executor-tagged scope
        # (codex_agent, claude_agent, ...); panels carry agent_panel.
        is_single_agent = scope.endswith("_agent")
        if not (is_single_agent or scope == "agent_panel") or item.get("panel_request_id"):
            continue
        weight = int(item.get("panel_runs") or 1) if scope == "agent_panel" else 1
        # Credit only the explicitly-targeted facts. Distinguish "no target was
        # given" (key present but empty -> credit nothing, never the bundle) from
        # a legacy result predating the field (key absent -> fall back to bundle).
        if "primary_fact_ids" in item:
            credited = item["primary_fact_ids"] or []
        else:
            credited = item.get("checked_fact_ids") or []
        for fact_id in credited:
            counts[str(fact_id)] = counts.get(str(fact_id), 0) + weight
    return counts


def build_context(project_root: Path, *, query: str | None = None, limit: int = 5) -> dict[str, Any]:
    source = source_file(project_root)
    tasks = list_tasks(project_root)
    task_pool = load_task_pool(project_root)
    frontier = load_frontier_index(project_root)
    frontier_report = frontier_health(project_root)
    verifications = latest_results(project_root)
    agent_verifications = [
        item
        for item in verifications
        if item.get("backend") in {"agent", "panel"}
        or str(item.get("verification_scope") or "").endswith("_agent")
        or item.get("verification_scope") == "agent_panel"
    ]
    structural_prechecks = [item for item in verifications if item not in agent_verifications]
    facts = validate_project_facts(project_root, rebuild=False)
    config = read_json(project_root / ".iteris" / "config.json", default={})
    search_results = search_memory(project_root, query, limit=limit) if query else []
    stale_tasks = stale_status_tasks(task_pool)
    streaks = rejection_streaks(verifications)
    passed_counts = keystone_verification_counts(verifications)
    under_verified_keystones = [
        item for item in keystone_facts(project_root, verification_counts=passed_counts) if item["under_verified"]
    ]
    reference_fetch_failures = [
        {"arxiv_id": event.get("arxiv_id"), "timestamp": event.get("timestamp"), "path": event.get("path")}
        for event in read_events(project_root)
        if event.get("event_type") == "arxiv_reference_fetch" and not event.get("source_ok") and not event.get("pdf_ok")
    ]
    status_md_stale_hours = _status_md_stale_hours(project_root)
    stale_verifications = stale_verification_requests(project_root)
    guidance_parts: list[str] = []
    if stale_verifications:
        guidance_parts.append(
            "A stale_verifications entry is a verification request past the timeout with no result. "
            "If verifier_process_alive is false the verifier died: salvage its output with "
            "`iteris tool verify finalize . --request-id <id> --json`, or resubmit the request. "
            "Never park the whole loop waiting on a single result file."
        )
    if stale_tasks or streaks:
        guidance_parts.append(
            "Stale running/review tasks are unharvested debt: inspect or reset them before launching new work. "
            "A rejection streak means stop submitting proof revisions of that claim; "
            "run a falsification/counterexample task or change the decomposition first."
        )
    if under_verified_keystones:
        guidance_parts.append(
            "Keystone facts (high in-degree: predecessors plus `fact:` citations in bodies) with a single passed verification concentrate risk: "
            "run `iteris tool verify panel . --mode fact ... --runs 2` on them before building further on top."
        )
    if reference_fetch_failures:
        guidance_parts.append(
            "Some arXiv references failed to fetch (no source and no PDF); do not cite them as read evidence — refetch or find an alternate source."
        )
    if status_md_stale_hours is not None:
        guidance_parts.append(
            f"STATUS.md is ~{status_md_stale_hours}h behind project activity; refresh it (current phase, case ledger, key open items) at the next checkpoint."
        )
    attention = {
        "stale_tasks": stale_tasks,
        "rejection_streaks": streaks,
        "stale_verifications": stale_verifications,
        "under_verified_keystones": under_verified_keystones,
        "reference_fetch_failures": reference_fetch_failures,
        "status_md_stale_hours": status_md_stale_hours,
        "guidance": " ".join(guidance_parts),
    }
    return {
        "project_path": str(project_root),
        "unread_messages": unread_summary(project_root),
        "source_file": str(source.relative_to(project_root)) if source else None,
        "status_text": _read_short(project_root / "STATUS.md"),
        "roadmap_text": _read_short(project_root / "ROADMAP.md"),
        "fact_count": facts["count"],
        "fact_type_counts": facts.get("fact_type_counts") or {},
        "attention": attention,
        "fact_index_records": _jsonl_count(project_root / "memory" / "facts" / "FACT_INDEX.jsonl"),
        "artifact_index_records": _jsonl_count(project_root / "artifacts" / "ARTIFACT_INDEX.jsonl"),
        "facts_ok": facts["ok"],
        "workflow_authority": "Use tasks/TASK_POOL.json and memory/facts/FRONTIER_INDEX.json for current work; open_tasks are legacy task-board entries.",
        "open_tasks": [task for task in tasks if task.get("status", "open") == "open"],
        "task_pool": task_pool,
        "frontier_index": frontier,
        "frontier_summary": frontier_summary(frontier),
        "frontier_health": frontier_report,
        "ready_pool_tasks": [task for task in task_pool.get("tasks", []) if isinstance(task, dict) and task.get("status") == "ready"],
        "artifact_layout": artifact_layout_summary(),
        "verification_results": [_verification_summary(item) for item in agent_verifications[-limit:]],
        "structural_precheck_results": [_verification_summary(item) for item in structural_prechecks[-limit:]],
        "git": git_status(project_root),
        "config": config,
        "search_query": query,
        "search_results": search_results,
        "recommended_commands": [
            "iteris tool context . --json",
            "iteris tool memory search . --query \"<query>\" --json",
            "iteris tool memory add-fact . --source-task <task-id> --claim-summary \"...\" --statement \"...\"",
            "iteris tool task list . --json",
            "iteris tool task pool show . --json",
            "iteris tool task pool select-ready . --json",
            "iteris tool frontier show . --json",
            "iteris tool frontier refresh . --json",
            "iteris tool frontier health . --json",
            "iteris tool agent explore . --focus \"<frontier>\" --detach --json",
            "iteris tool agent execute . --task-id <task-id> --mode foundation|proof|experiment|algorithm --detach --json",
            "iteris tool agent runs . --json",
            "iteris tool agent wait . --run-id <run-id> --timeout 3600 --json",
            "iteris tool agent inspect . --run-id <run-id> --json",
            "iteris tool artifact gate . --json",
            "iteris tool artifact search . \"<artifact query>\" --json",
            "iteris tool theorem search . --query \"<mathematical statement>\" --json",
            "iteris tool theorem fetch . --arxiv-id <id-or-url> --json",
            "iteris tool verify submit . --backend agent --mode fact --claim \"<fact claim>\" --artifact memory/facts/<fact-file>.md --json",
            "iteris tool verify finalize . --request-id <request-id> --json",
            "iteris tool memory promote-fact . --fact-id <fact:id> --verification <request-id> --json",
            "iteris tool verify submit . --backend agent --mode assembly --claim \"<goal summary>\" --target-artifact results/<problem-id>/answer.md --json",
            "iteris tool verify submit . --backend agent --mode goal_success --claim \"<original goal>\" --artifact <source-file> --artifact results/<problem-id>/answer.md --artifact verification/results/<assembly-result>.json --target-artifact results/<problem-id>/answer.md --json",
            "iteris tool goal finalize . --target-artifact results/<problem-id>/answer.md --json",
            "iteris tool logs bundle . --session <tmux-session> --json",
            "iteris tool verify status . --json",
            "iteris tool git status .",
            "iteris tool git checkpoint . --message \"checkpoint: <summary>\"",
        ],
    }


STATUS_MD_STALE_THRESHOLD_HOURS = 1.0


def _status_md_stale_hours(project_root: Path) -> float | None:
    """Hours STATUS.md lags behind the newest project activity, if over threshold.

    Compares file mtimes (agents rewrite STATUS.md whole, so mtime is reliable;
    the embedded last_updated text is too free-form to parse). Returns None when
    fresh, missing, or the lag is under the threshold.
    """
    status_path = project_root / "STATUS.md"
    if not status_path.exists():
        return None
    activity_paths = [
        project_root / "memory" / "scratch" / "events.jsonl",
        project_root / "memory" / "facts" / "FACT_INDEX.jsonl",
        project_root / "tasks" / "TASK_POOL.json",
    ]
    mtimes = [path.stat().st_mtime for path in activity_paths if path.exists()]
    if not mtimes:
        return None
    lag_hours = (max(mtimes) - status_path.stat().st_mtime) / 3600.0
    if lag_hours < STATUS_MD_STALE_THRESHOLD_HOURS:
        return None
    return round(lag_hours, 1)


def _read_short(path: Path, limit: int = 2000) -> str:
    if not path.exists():
        return ""
    text = path.read_text(encoding="utf-8", errors="replace")
    return text[:limit]


def _jsonl_count(path: Path) -> int:
    if not path.exists():
        return 0
    return sum(1 for line in path.read_text(encoding="utf-8", errors="replace").splitlines() if line.strip())


def _verification_summary(item: dict[str, Any]) -> dict[str, Any]:
    request_id = str(item.get("request_id") or "")
    return {
        "request_id": request_id,
        "result_path": f"verification/results/{request_id}.json" if request_id else None,
        "backend": item.get("backend"),
        "mode": item.get("mode"),
        "verdict": item.get("verdict"),
        "passed": item.get("passed"),
        "strict_verdict": item.get("strict_verdict"),
        "target_artifact": item.get("target_artifact"),
        "summary": _truncate(str(item.get("summary") or ""), 500),
        "critical_errors": item.get("critical_errors") or [],
        "gaps": item.get("gaps") or [],
        "checked_fact_ids": (item.get("checked_fact_ids") or [])[:12],
        "checked_artifact_count": len(item.get("checked_artifacts") or []),
        "created_at": item.get("created_at"),
    }


def _truncate(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[: limit - 3].rstrip() + "..."


def context(
    project_path: str = typer.Argument(".", help="Iteris project path."),
    query: str | None = typer.Option(None, "--query", "-q", help="Optional memory search query to include."),
    limit: int = typer.Option(5, "--limit", "-n", help="Maximum search and verification records."),
    json_output: bool = typer.Option(False, "--json", help="Print machine-readable JSON."),
) -> None:
    """Print the project context an agent should read before working."""
    root = require_project(project_path)
    result = build_context(root, query=query, limit=limit)
    if json_output:
        typer.echo(json.dumps(result, indent=2, ensure_ascii=False))
        return
    log.header("Project context")
    log.key_value(
        {
            "Project": result["project_path"],
            "Source": result["source_file"] or "(none)",
            "Facts": f"{result['fact_count']} ({'ok' if result['facts_ok'] else 'invalid'})",
            "Frontiers": str(result["frontier_summary"]["active_frontiers"]),
            "Open tasks": str(len(result["open_tasks"])),
            "Ready pool tasks": str(len(result["ready_pool_tasks"])),
            "Verification results": str(len(result["verification_results"])),
            "Git": _git_summary(result["git"]),
        }
    )
    task_rows = [(task["task_id"], task.get("category", "?"), task.get("objective", "")[:180]) for task in result["open_tasks"][:limit]]
    log.results_table(task_rows or [("none", "skipped", "no open tasks")], title="Open tasks")
    attention = result.get("attention") or {}
    attention_rows = [
        (str(item["task_id"]), str(item["status"]), f"{item['hours_in_status']}h in status")
        for item in attention.get("stale_tasks") or []
    ] + [
        (str(item["claim_key"])[:60], "rejections", f"{item['consecutive_rejections']} consecutive ({item['attempts']} attempts)")
        for item in attention.get("rejection_streaks") or []
    ] + [
        (
            str(item["request_id"])[:60],
            "verification",
            f"{item['age_minutes']}min without result"
            + (", verifier dead" if item.get("verifier_process_alive") is False else ""),
        )
        for item in attention.get("stale_verifications") or []
    ] + [
        (str(item["fact_id"])[:60], "keystone", f"in-degree {item['in_degree']}, {item['passed_verifications']} passed verification(s)")
        for item in attention.get("under_verified_keystones") or []
    ] + [
        (str(item["arxiv_id"]), "fetch-failed", "no source and no PDF retrieved")
        for item in attention.get("reference_fetch_failures") or []
    ] + (
        [("STATUS.md", "stale", f"{attention['status_md_stale_hours']}h behind project activity")]
        if attention.get("status_md_stale_hours") is not None
        else []
    )
    if attention_rows:
        log.results_table(attention_rows, title="Attention")
    if result["search_results"]:
        rows = []
        for item in result["search_results"]:
            label = item.get("fact_id") or item.get("channel") or Path(item.get("_path", "record")).name
            summary = item.get("claim_summary") or json.dumps(item.get("record", item), ensure_ascii=False)[:180]
            rows.append((str(label), f"{item.get('_score', 0):.3f}", str(summary)[:180]))
        log.results_table(rows, title="Memory search")
    log.results_table([(cmd, "", "") for cmd in result["recommended_commands"]], title="Recommended commands")


def _git_summary(result: dict[str, Any]) -> str:
    if not result.get("repo"):
        return "not initialized"
    dirty = "dirty" if result.get("dirty") else "clean"
    return f"{result.get('branch')} ({dirty})"

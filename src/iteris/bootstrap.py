"""Deterministic bootstrap intake for a project source problem."""

from __future__ import annotations

import re
from pathlib import Path

from iteris.memory.facts import rebuild_fact_index, write_fact
from iteris.memory.scratch import append as scratch_append
from iteris.project import now_iso, now_stamp, project_id_from_path, source_file, write_json
from iteris.tasks import add_task, update_pool_task
from iteris.verification.local import verify_local


def extract_problem_statement(text: str) -> str:
    match = re.search(r"\\begin\{problem\}(.*?)\\end\{problem\}", text, re.DOTALL)
    if match:
        return " ".join(match.group(1).split())
    compact = " ".join(text.split())
    return compact[:800]


def summarize_source(text: str) -> dict[str, object]:
    statement = extract_problem_statement(text)
    keywords = extract_keywords(text)
    return {
        "problem_statement": statement,
        "keywords": keywords,
        "line_count": len(text.splitlines()),
        "char_count": len(text),
    }


def extract_keywords(text: str, *, limit: int = 12) -> list[str]:
    stopwords = {
        "about",
        "after",
        "before",
        "between",
        "could",
        "every",
        "given",
        "where",
        "which",
        "while",
        "with",
        "without",
        "there",
        "their",
        "prove",
        "problem",
        "theorem",
    }
    counts: dict[str, int] = {}
    for raw in re.findall(r"[A-Za-z][A-Za-z0-9_-]{3,}", text):
        token = raw.strip("_-")
        lower = token.lower()
        if lower in stopwords:
            continue
        counts[token] = counts.get(token, 0) + 1
    ranked = sorted(counts, key=lambda token: (-counts[token], token.lower()))
    return ranked[:limit]


def run_once(project_root: Path) -> dict[str, object]:
    project_root = project_root.resolve()
    src = source_file(project_root)
    if src is None:
        raise FileNotFoundError("project has no source file")
    text = src.read_text(encoding="utf-8", errors="replace")
    summary = summarize_source(text)
    run_id = f"run-{now_stamp()}"
    run_dir = project_root / "artifacts" / "runs" / run_id
    run_dir.mkdir(parents=True, exist_ok=True)

    task = add_task(
        project_root,
        title=f"initial-source-exploration-{run_id}",
        category="foundation",
        objective="Extract the source problem, create first memory records, and submit a structural verification claim.",
        claim_ceiling="submitted",
    )
    scratch_append(
        project_root,
        "observations",
        {
            "event_type": "source_problem_intake",
            "source": str(src.relative_to(project_root)),
            "summary": summary,
            "task_id": task["task_id"],
        },
    )

    exploration_md = run_dir / "exploration.md"
    result_md = run_dir / "result.md"
    statement = str(summary["problem_statement"])
    keywords = ", ".join(summary["keywords"]) or "none detected"
    exploration_md.write_text(
        "# Iteris Bootstrap Exploration\n\n"
        f"Run: `{run_id}`\n\n"
        f"Source: `{src.relative_to(project_root)}`\n\n"
        "## Problem Statement\n\n"
        f"{statement}\n\n"
        "## Detected Structure\n\n"
        f"- Keywords: {keywords}\n"
        "- Primary object: source problem recorded from the project input.\n"
        "- Initial route: source and foundation audit before strong claims.\n\n"
        "## Next Candidate Tasks\n\n"
        "1. Freeze the exact statement, assumptions, and target artifact path.\n"
        "2. Identify definitions, known results, and missing proof or implementation gates.\n"
        "3. Create durable facts only for reusable claims with explicit evidence.\n",
        encoding="utf-8",
    )
    result_md.write_text(
        "# Iteris Bootstrap Result\n\n"
        "This run produced a first durable memory fact and a structural precheck result.\n\n"
        f"Problem: {statement}\n\n"
        "Claim ceiling: submitted until independent mathematical verification is requested.\n",
        encoding="utf-8",
    )

    project_id = project_id_from_path(project_root)
    fact_id = f"fact:{project_id}:{run_id}:source-problem"
    fact_path = write_fact(
        project_root,
        fact_id=fact_id,
        source_task=task["task_id"],
        fact_type="source_problem",
        claim_summary="The source problem has been recorded for Iteris project work.",
        statement=statement,
        notes=f"Source file: {src.relative_to(project_root)}. Exploration artifact: {exploration_md.relative_to(project_root)}.",
    )
    rebuilt = rebuild_fact_index(project_root)
    verification = verify_local(
        project_root,
        mode="source",
        claim="The bootstrap run correctly extracted and recorded the source problem for first-stage planning.",
        artifacts=[exploration_md.relative_to(project_root), fact_path.relative_to(project_root)],
    )
    scratch_append(
        project_root,
        "decisions",
        {
            "event_type": "bootstrap_run_completed",
            "run_id": run_id,
            "task_id": task["task_id"],
            "fact_id": fact_id,
            "verification_request_id": verification["request_id"],
            "decision": "continue_with_foundation_and_theorem_search",
        },
    )
    # The intake work happens inline right here, so close the task instead of
    # leaving a permanently-"running" umbrella entry that poisons liveness
    # monitoring for the whole run.
    task["status"] = "done"
    write_json(project_root / "tasks" / f"{task['task_id']}.json", task)
    update_pool_task(
        project_root,
        task["task_id"],
        status="done",
        append_notes=["Bootstrap intake completed inline; closed by run_once."],
    )
    write_json(
        run_dir / "run_summary.json",
        {
            "schema_version": "iteris.bootstrap_run_summary.v0",
            "run_id": run_id,
            "source": str(src.relative_to(project_root)),
            "task": task,
            "fact_path": str(fact_path.relative_to(project_root)),
            "fact_index_records": rebuilt,
            "verification": verification,
            "created_at": now_iso(),
        },
    )
    status = project_root / "STATUS.md"
    status.write_text(
        "phase: bootstrap_run_completed\n"
        f"last_run: {run_id}\n"
        f"last_verification: {verification['request_id']}\n"
        f"last_updated: {now_iso()}\n",
        encoding="utf-8",
    )
    return {
        "run_id": run_id,
        "run_dir": str(run_dir),
        "task_id": task["task_id"],
        "fact_id": fact_id,
        "fact_path": str(fact_path),
        "verification_request_id": verification["request_id"],
        "verification_verdict": verification["verdict"],
    }

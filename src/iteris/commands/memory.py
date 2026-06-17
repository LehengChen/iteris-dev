"""Memory commands."""

from __future__ import annotations

import json
from pathlib import Path

import typer

from iteris import log
from iteris.memory.facts import find_fact_file, rebuild_fact_index, update_fact_metadata, validate_project_facts, write_fact
from iteris.memory.search import search_memory
from iteris.memory.scratch import append as scratch_append
from iteris.memory.scratch import append_event
from iteris.project import is_project, now_stamp, project_id_from_path, require_project, slugify
from iteris.verification.local import latest_results

app = typer.Typer(help="Inspect and maintain project memory.")


@app.command("validate")
def validate(
    project_path: str = typer.Argument(".", help="Iteris project path."),
    rebuild: bool = typer.Option(False, "--rebuild/--no-rebuild", help="Rebuild FACT_INDEX.jsonl after validation."),
    json_output: bool = typer.Option(False, "--json", help="Print machine-readable JSON."),
) -> None:
    """Validate durable facts without changing project state by default."""
    root = require_project(project_path)
    result = validate_project_facts(root, rebuild=rebuild)
    if json_output:
        typer.echo(json.dumps(result, indent=2, ensure_ascii=False))
        return
    rows = []
    for item in result["results"]:
        status = "ok" if item["ok"] else "error"
        detail = "; ".join(item["errors"] or item["warnings"] or ["valid"])
        rows.append((Path(item["path"]).name, status, detail))
    log.results_table(rows or [("facts", "ok", "no fact files yet")], title="Fact validation")
    if rebuild:
        log.success(f"FACT_INDEX records rebuilt: {result['rebuilt']}")
    else:
        log.success("Validation complete; FACT_INDEX was not rebuilt")


@app.command("reindex")
def reindex(project_path: str = typer.Argument(".", help="Iteris project path.")) -> None:
    """Rebuild FACT_INDEX.jsonl without printing each fact."""
    root = require_project(project_path)
    count = rebuild_fact_index(root)
    log.success(f"Rebuilt FACT_INDEX.jsonl with {count} record(s)")


@app.command("search")
def search(
    project_path: str = typer.Argument(".", help="Iteris project path, or first query word when run from a project."),
    terms: list[str] = typer.Argument(None, help="Query words. Alternative to --query."),
    query: str | None = typer.Option(None, "--query", "-q", help="Search query."),
    limit: int = typer.Option(10, "--limit", "-n", help="Maximum results."),
    facts_only: bool = typer.Option(False, "--facts-only", help="Do not search scratch memory or family ledgers."),
    local_only: bool = typer.Option(False, "--local-only", help="Skip family ledgers (debugging opt-out; family scope is searched by default)."),
    json_output: bool = typer.Option(False, "--json", help="Print machine-readable JSON."),
) -> None:
    """BM25 search over fact index, scratch memory, and family ledgers (auto-detected)."""
    positional = [project_path, *(terms or [])]
    if query is None and not is_project(project_path):
        root = require_project(".")
        resolved_query = " ".join(positional).strip()
    else:
        root = require_project(project_path)
        resolved_query = (query or " ".join(terms or [])).strip()
    if not resolved_query:
        raise typer.BadParameter("provide a query with --query or positional words")
    results = search_memory(
        root,
        resolved_query,
        limit=limit,
        include_scratch=not facts_only,
        include_family=not (facts_only or local_only),
    )
    if json_output:
        typer.echo(json.dumps(results, indent=2, ensure_ascii=False))
        return
    rows = []
    for item in results:
        label = item.get("fact_id") or item.get("origin_fact_id") or item.get("channel") or item.get("_path", "record")
        if item.get("scope") == "family":
            label = f"[family] {label}"
        summary = item.get("claim_summary") or json.dumps(item.get("record", item), ensure_ascii=False)[:180]
        rows.append((str(label), f"{item.get('_score', 0):.3f}", str(summary)[:220]))
    log.results_table(rows or [("no matches", "0", resolved_query)], title="Memory search")


@app.command("append")
def append(
    project_path: str = typer.Argument(".", help="Iteris project path."),
    channel: str = typer.Option("observations", "--channel", "-c", help="Scratch channel."),
    text: str = typer.Option(..., "--text", "-t", help="Observation text."),
) -> None:
    """Append a plain-text scratch memory record."""
    root = require_project(project_path)
    path = scratch_append(root, channel, {"text": text, "event_type": "manual_append"})
    log.success(f"Appended to {path.relative_to(root)}")


@app.command("add-fact")
def add_fact(
    project_path: str = typer.Argument(".", help="Iteris project path."),
    source_task: str = typer.Option(..., "--source-task", help="Task id that produced this fact."),
    claim_summary: str = typer.Option(..., "--claim-summary", help="Short claim summary."),
    statement: str = typer.Option(..., "--statement", help="Fact statement body."),
    fact_id: str | None = typer.Option(None, "--fact-id", help="Full fact id. Auto-generated when omitted."),
    fact_type: str = typer.Option("claim", "--fact-type", help="Durable fact type."),
    status: str = typer.Option("submitted", "--status", help="Fact status."),
    claim_policy: str = typer.Option("stable_claim", "--claim-policy", help="Claim policy label."),
    predecessor: list[str] | None = typer.Option(None, "--predecessor", help="Predecessor fact id. Repeatable."),
    notes: str = typer.Option("", "--notes", help="Optional notes section."),
    verification: str | None = typer.Option(None, "--verification", help="Optional verification id."),
    reindex: bool = typer.Option(True, "--reindex/--no-reindex", help="Rebuild FACT_INDEX.jsonl after writing."),
    json_output: bool = typer.Option(False, "--json", help="Print machine-readable JSON."),
) -> None:
    """Create a durable fact file for a stable claim and optionally update FACT_INDEX.jsonl."""
    root = require_project(project_path)
    if status == "verified":
        raise typer.BadParameter(
            "add-fact cannot mint verified facts; create the fact as 'submitted', run "
            "`iteris tool verify submit . --backend agent --mode fact ...`, then "
            "`iteris tool memory promote-fact . --fact-id ... --verification <request-id>`"
        )
    project_id = project_id_from_path(root)
    resolved_fact_id = fact_id or f"fact:{project_id}:{slugify(claim_summary, 48)}:{now_stamp()}"
    resubmit = find_fact_file(root, resolved_fact_id) is not None
    path = write_fact(
        root,
        fact_id=resolved_fact_id,
        source_task=source_task,
        claim_summary=claim_summary,
        statement=statement,
        status=status,
        fact_type=fact_type,
        predecessors=predecessor or [],
        notes=notes,
        verification=verification,
        claim_policy=claim_policy,
    )
    rebuilt = rebuild_fact_index(root) if reindex else 0
    append_event(
        root,
        "fact_update" if resubmit else "fact_add",
        {
            "fact_id": resolved_fact_id,
            "path": str(path.relative_to(root)),
            "resubmit": resubmit,
            "reindexed": reindex,
        },
    )
    payload = {
        "fact_id": resolved_fact_id,
        "path": str(path.relative_to(root)),
        "resubmit": resubmit,
        "reindexed": reindex,
        "rebuilt": rebuilt,
    }
    if json_output:
        typer.echo(json.dumps(payload, indent=2, ensure_ascii=False))
        return
    log.success(f"{'Updated' if resubmit else 'Added'} fact {resolved_fact_id}")
    if reindex:
        log.info(f"FACT_INDEX records rebuilt: {rebuilt}")


@app.command("promote-fact")
def promote_fact(
    project_path: str = typer.Argument(".", help="Iteris project path."),
    fact_id: str = typer.Option(..., "--fact-id", help="Fact id to promote."),
    verification: str = typer.Option(..., "--verification", help="Passed verification request id."),
    status: str = typer.Option("verified", "--status", help="Target fact status."),
    review_level: str = typer.Option("verified", "--review-level", help="Review level to write into fact frontmatter."),
    force: bool = typer.Option(False, "--force", help="Allow promotion without a passed matching verification result."),
    json_output: bool = typer.Option(False, "--json", help="Print machine-readable JSON."),
) -> None:
    """Promote a fact after a matching verification result has passed."""
    root = require_project(project_path)
    verification_result = _find_verification(root, verification)
    if not force:
        if verification_result is None:
            raise typer.BadParameter(f"verification result not found: {verification}")
        # Demotion to rejected is evidenced by a FAILED verification; every
        # other target status requires a passed one.
        if status != "rejected" and not verification_result.get("passed"):
            raise typer.BadParameter(f"verification did not pass: {verification}")
        checked = verification_result.get("checked_fact_ids") or []
        if fact_id not in checked:
            raise typer.BadParameter(f"verification result does not check fact id: {fact_id}")

    path = update_fact_metadata(root, fact_id=fact_id, status=status, verification=verification, review_level=review_level)
    rebuilt = rebuild_fact_index(root)
    append_event(
        root,
        "fact_promote",
        {
            "fact_id": fact_id,
            "path": str(path.relative_to(root)),
            "status": status,
            "verification": verification,
            "forced": force,
            "rebuilt": rebuilt,
        },
    )
    payload = {
        "fact_id": fact_id,
        "path": str(path.relative_to(root)),
        "status": status,
        "verification": verification,
        "forced": force,
        "rebuilt": rebuilt,
    }
    if json_output:
        typer.echo(json.dumps(payload, indent=2, ensure_ascii=False))
        return
    log.success(f"Promoted fact {fact_id} to {status}")
    log.info(f"FACT_INDEX records rebuilt: {rebuilt}")


def _find_verification(project_root: Path, request_id: str) -> dict[str, object] | None:
    for result in latest_results(project_root):
        if result.get("request_id") == request_id:
            return result
    return None

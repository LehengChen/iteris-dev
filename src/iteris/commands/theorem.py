"""Theorem-search commands."""

from __future__ import annotations

import hashlib
import json

import typer

from iteris import log
from iteris.memory.scratch import append as scratch_append
from iteris.project import read_json, require_project, write_json
from iteris.tools.arxiv import fetch_arxiv_reference
from iteris.tools.theorem_search import search_arxiv_theorems

app = typer.Typer(help="Search external theorem databases and persist results.")


@app.command("search")
def search(
    project_path: str = typer.Argument(".", help="Iteris project path."),
    query: str = typer.Option(..., "--query", "-q", help="Mathematical statement or search query."),
    num_results: int = typer.Option(10, "--num-results", "-n", help="Maximum theorem-search results."),
    timeout_seconds: int = typer.Option(30, "--timeout", help="HTTP timeout in seconds."),
    save: bool = typer.Option(True, "--save/--no-save", help="Persist results under artifacts/references/."),
    refresh: bool = typer.Option(False, "--refresh", help="Bypass the project-local query cache and hit the endpoint again."),
    json_output: bool = typer.Option(False, "--json", help="Print machine-readable JSON."),
) -> None:
    """Search leansearch.net/thm and save project-local evidence (cached per query)."""
    root = require_project(project_path)
    cache_key = hashlib.sha256(f"{query.strip().lower()}|{num_results}".encode("utf-8")).hexdigest()[:24]
    cache_path = root / "artifacts" / "references" / "theorem_search" / f"query-{cache_key}.json"
    cached = None if refresh else read_json(cache_path, default=None)
    from_cache = isinstance(cached, dict) and cached.get("results") is not None
    payload = cached if from_cache else search_arxiv_theorems(query=query, num_results=num_results, timeout_seconds=timeout_seconds)
    saved_path = None
    if save:
        saved_path = cache_path
        if not from_cache:
            write_json(saved_path, payload)
        scratch_append(
            root,
            "events",
            {
                "event_type": "theorem_search",
                "query": query,
                "count": payload.get("count", 0),
                "cached": from_cache,
                "path": str(saved_path.relative_to(root)),
            },
        )
    output = dict(payload)
    output["cached"] = from_cache
    if saved_path is not None:
        output["saved_path"] = str(saved_path.relative_to(root))
    if json_output:
        typer.echo(json.dumps(output, indent=2, ensure_ascii=False))
        return
    rows = [(item.get("arxiv_id", ""), item.get("theorem_id", ""), item.get("title", "")[:120]) for item in payload.get("results", [])]
    log.results_table(rows or [("none", "none", "no theorem-search results")], title="Theorem search")
    if saved_path is not None:
        log.info(f"Saved: {saved_path.relative_to(root)}")


@app.command("fetch")
def fetch(
    project_path: str = typer.Argument(".", help="Iteris project path."),
    arxiv_id: str = typer.Option(..., "--arxiv-id", help="arXiv id or arxiv.org URL."),
    timeout_seconds: int = typer.Option(60, "--timeout", help="HTTP timeout in seconds."),
    include_pdf: bool = typer.Option(False, "--include-pdf/--source-first-only", help="Also fetch and parse the PDF even when source is available."),
    json_output: bool = typer.Option(False, "--json", help="Print machine-readable JSON."),
) -> None:
    """Fetch an arXiv reference source-first, with PDF text fallback."""
    root = require_project(project_path)
    payload = fetch_arxiv_reference(root, arxiv_id=arxiv_id, timeout_seconds=timeout_seconds, include_pdf=include_pdf)
    scratch_append(
        root,
        "events",
        {
            "event_type": "arxiv_reference_fetch",
            "arxiv_id": payload["arxiv_id"],
            "path": payload["manifest_path"],
            "source_ok": bool(payload.get("source", {}).get("ok")),
            "pdf_ok": bool((payload.get("pdf") or {}).get("ok")),
        },
    )
    if json_output:
        typer.echo(json.dumps(payload, indent=2, ensure_ascii=False))
        return
    source = payload.get("source") or {}
    pdf = payload.get("pdf") or {}
    rows = [
        ("source", "ok" if source.get("ok") else "failed", ", ".join(source.get("source_files") or source.get("errors") or [])),
        ("pdf", "ok" if pdf.get("ok") else ("skipped" if not pdf else "failed"), str(pdf.get("text_path") or pdf.get("errors") or "")),
    ]
    log.results_table(rows, title=f"arXiv {payload['arxiv_id']}")
    log.info(f"Manifest: {payload['manifest_path']}")

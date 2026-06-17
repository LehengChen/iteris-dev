"""Verification commands."""

from __future__ import annotations

import json
from pathlib import Path

import typer

from iteris import log
from iteris.project import require_project
from iteris.verification.agent import finalize_agent_run, verify_agent
from iteris.verification.local import (
    STALE_VERIFICATION_MINUTES,
    _verifier_process_alive,
    latest_results,
    verify_local,
)
from iteris.verification.panel import DEFAULT_PANEL_RUNS, verify_panel

app = typer.Typer(help="Submit and inspect verification requests.")


@app.command("panel")
def panel(
    project_path: str = typer.Argument(".", help="Iteris project path."),
    mode: str = typer.Option("fact", "--mode", "-m", help="Verification mode."),
    claim: str = typer.Option(..., "--claim", "-c", help="Claim to verify."),
    artifact: list[str] = typer.Option([], "--artifact", "-a", help="Artifact path relative to project. Repeatable."),
    fact_id: list[str] = typer.Option([], "--fact-id", help="Fact id to verify. Repeatable."),
    target_artifact: str | None = typer.Option(None, "--target-artifact", "-o", help="Terminal artifact path for assembly or goal-success verification."),
    runs: int = typer.Option(DEFAULT_PANEL_RUNS, "--runs", "-n", help="Number of independent verification agents."),
    executor: str | None = typer.Option(None, "--executor", "-e", help="Verifier CLI: codex or claude. Defaults to $ITERIS_VERIFICATION_EXECUTOR, then $ITERIS_EXECUTOR, then codex."),
    json_output: bool = typer.Option(False, "--json", help="Print machine-readable JSON."),
) -> None:
    """Run N independent verification agents on one claim; passes only on unanimous accept.

    Use for keystone facts (high predecessor in-degree): verification depth should
    scale with how load-bearing the claim is.
    """
    root = require_project(project_path)
    artifacts = [Path(a) for a in artifact]
    if mode in {"assembly", "goal_success"} and target_artifact and Path(target_artifact) not in artifacts:
        artifacts.append(Path(target_artifact))
    result = verify_panel(
        root,
        mode=mode,
        claim=claim,
        artifacts=artifacts,
        fact_ids=fact_id,
        target_artifact=Path(target_artifact) if target_artifact else None,
        runs=runs,
        executor=executor,
    )
    if json_output:
        typer.echo(json.dumps(result, indent=2, ensure_ascii=False))
        return
    log.key_value(
        {
            "Request": result["request_id"],
            "Mode": result["mode"],
            "Verdict": result["verdict"],
            "Passed": str(result["passed"]),
            "Seats": ", ".join(f"{seat.get('request_id') or 'error'}={seat.get('verdict')}" for seat in result["panel_seats"]),
            "Summary": result["summary"],
        }
    )


@app.command("submit")
def submit(
    project_path: str = typer.Argument(".", help="Iteris project path."),
    mode: str = typer.Option("source", "--mode", "-m", help="Verification mode."),
    claim: str = typer.Option(..., "--claim", "-c", help="Claim to verify."),
    artifact: list[str] = typer.Option([], "--artifact", "-a", help="Artifact path relative to project. Repeatable."),
    fact_id: list[str] = typer.Option([], "--fact-id", help="Fact id to verify or require in an assembly. Repeatable."),
    target_artifact: str | None = typer.Option(None, "--target-artifact", "-o", help="Terminal artifact path for assembly or goal-success verification."),
    backend: str = typer.Option("agent", "--backend", help="Verification backend: agent or structural."),
    executor: str | None = typer.Option(None, "--executor", "-e", help="Verifier CLI (agent backend): codex or claude. Defaults to $ITERIS_VERIFICATION_EXECUTOR, then $ITERIS_EXECUTOR, then codex."),
    json_output: bool = typer.Option(False, "--json", help="Print machine-readable JSON."),
) -> None:
    """Run verification over an evidence bundle."""
    root = require_project(project_path)
    artifacts = [Path(a) for a in artifact]
    if mode in {"assembly", "goal_success"} and target_artifact and Path(target_artifact) not in artifacts:
        artifacts.append(Path(target_artifact))
    if backend == "agent":
        result = verify_agent(
            root,
            mode=mode,
            claim=claim,
            artifacts=artifacts,
            fact_ids=fact_id,
            target_artifact=Path(target_artifact) if target_artifact else None,
            executor=executor,
        )
    elif backend == "structural":
        result = verify_local(
            root,
            mode=mode,
            claim=claim,
            artifacts=artifacts,
            fact_ids=fact_id,
            target_artifact=Path(target_artifact) if target_artifact else None,
        )
    else:
        raise typer.BadParameter("backend must be 'agent' or 'structural'")
    if json_output:
        typer.echo(json.dumps(result, indent=2, ensure_ascii=False))
        return
    log.key_value(
        {
            "Request": result["request_id"],
            "Mode": result["mode"],
            "Verdict": result["verdict"],
            "Passed": str(result["passed"]),
            "Summary": result["summary"],
        }
    )


@app.command("finalize")
def finalize(
    project_path: str = typer.Argument(".", help="Iteris project path."),
    request_id: str = typer.Option(..., "--request-id", "-r", help="Agent verification request id."),
    overwrite: bool = typer.Option(False, "--overwrite", help="Rewrite an existing normalized result."),
    json_output: bool = typer.Option(False, "--json", help="Print machine-readable JSON."),
) -> None:
    """Adopt a completed verification/agent_runs/<request-id>/verification.json result."""
    root = require_project(project_path)
    result = finalize_agent_run(root, request_id=request_id, overwrite=overwrite)
    if json_output:
        typer.echo(json.dumps(result, indent=2, ensure_ascii=False))
        return
    log.key_value(
        {
            "Request": result["request_id"],
            "Mode": result["mode"],
            "Verdict": result["verdict"],
            "Passed": str(result["passed"]),
            "Summary": result["summary"],
        }
    )


@app.command("wait")
def wait(
    project_path: str = typer.Argument(".", help="Iteris project path."),
    request_id: str = typer.Option(..., "--request-id", "-r", help="Verification request id to wait on."),
    timeout: int = typer.Option(1800, "--timeout", help="Maximum seconds to wait for the verdict."),
    poll_interval: float = typer.Option(10.0, "--poll-interval", help="Seconds between result checks."),
    json_output: bool = typer.Option(False, "--json", help="Print machine-readable JSON."),
) -> None:
    """Block until a verification verdict lands, then print it. Always returns.

    `iteris tool verify submit` already runs the verifier synchronously and
    returns the verdict, so a separate wait is only needed for a request
    submitted out-of-band. Use this instead of a hand-written
    `until [ -f results/<id>.json ]; do sleep ...; done` shell loop: such a
    loop that misses the result file (or whose verifier died) deadlocks the
    whole /goal turn. This command resolves three terminal states and never
    hangs: `done` (result present), `dead` (no result and the verifier process
    is gone — salvage with `verify finalize`), or `timeout` (Exit 124).
    """
    import time as _time

    root = require_project(project_path)
    if "/" in request_id or "\\" in request_id or ".." in request_id:
        raise typer.BadParameter(f"invalid request id: {request_id}")
    result_path = root / "verification" / "results" / f"{request_id}.json"
    request_path = root / "verification" / "requests" / f"{request_id}.json"
    deadline = _time.monotonic() + max(0, timeout)
    poll_interval = max(0.5, poll_interval)
    payload: dict[str, object]
    while True:
        verdict = None
        if result_path.exists():
            # A result written non-atomically could be observed mid-flush; a
            # transient decode/read error (or a non-object payload) means "not
            # ready yet", so keep polling rather than crash or report a bogus
            # verdict. (write_json is atomic now, but the guard is cheap.)
            try:
                loaded = json.loads(result_path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                loaded = None
            if isinstance(loaded, dict):
                verdict = loaded
        if verdict is not None:
            payload = {
                "status": "done",
                "request_id": request_id,
                "passed": verdict.get("passed"),
                "verdict": verdict.get("verdict"),
                "strict_verdict": verdict.get("strict_verdict"),
                "summary": verdict.get("summary"),
                "timed_out": False,
            }
            break
        alive = _verifier_process_alive(request_id) if request_path.exists() else None
        # alive is False only where /proc could be read and no live process
        # carries this request id: the verifier is gone and no result will ever
        # arrive, so stop waiting and point at the salvage path.
        if alive is False:
            payload = {
                "status": "dead",
                "request_id": request_id,
                "passed": None,
                "timed_out": False,
                "salvage": f"iteris tool verify finalize . --request-id {request_id} --json",
                "detail": "verifier process is gone and no result was written; finalize a completed agent_runs result or resubmit",
            }
            break
        if _time.monotonic() >= deadline:
            payload = {
                "status": "timeout",
                "request_id": request_id,
                "passed": None,
                "timed_out": True,
                "detail": f"no result after {timeout}s; verifier may still be running (stale threshold is {STALE_VERIFICATION_MINUTES} min) — re-check or salvage with verify finalize",
            }
            break
        _time.sleep(max(0.1, min(poll_interval, deadline - _time.monotonic())))
    if json_output:
        typer.echo(json.dumps(payload, indent=2, ensure_ascii=False))
    else:
        log.key_value(
            {
                "Request": str(payload["request_id"]),
                "Status": str(payload["status"]),
                "Passed": str(payload.get("passed")),
                "Verdict": str(payload.get("verdict") or payload.get("detail") or ""),
            }
        )
    if payload["status"] == "timeout":
        raise typer.Exit(124)
    if payload["status"] == "dead":
        raise typer.Exit(1)


@app.command("status")
def status(
    project_path: str = typer.Argument(".", help="Iteris project path."),
    request_id: str | None = typer.Argument(None, help="Optional request id."),
    json_output: bool = typer.Option(False, "--json", help="Print machine-readable JSON."),
) -> None:
    """Show verification results."""
    root = require_project(project_path)
    results = latest_results(root)
    if request_id:
        results = [item for item in results if item.get("request_id") == request_id]
    if json_output:
        typer.echo(json.dumps(results, indent=2, ensure_ascii=False))
        return
    rows = [(r["request_id"], r["verdict"], r["summary"][:220]) for r in results]
    log.results_table(rows or [("none", "skipped", "no verification results")], title="Verification results")


@app.command("serve")
def serve(
    host: str = typer.Option("127.0.0.1", "--host"),
    port: int = typer.Option(8092, "--port"),
) -> None:
    """Start the optional HTTP verification service."""
    try:
        import uvicorn
    except Exception as exc:  # pragma: no cover
        raise typer.BadParameter(f"uvicorn is not installed: {exc}") from exc
    uvicorn.run("iteris.verification.server:app", host=host, port=port, reload=False)

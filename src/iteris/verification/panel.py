"""Panel verification: N independent verification agents over one claim.

Keystone facts — the ones many later facts depend on — get the same
single-agent verification as routine lemmas, so a single missed flaw can
collapse the whole tree. A panel runs several independent verification agents
on the same evidence bundle in parallel and accepts only when every seat
accepts. The aggregate is persisted as a normal verification result, so
`promote-fact` can consume a panel request id exactly like a single-agent one.
"""

from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Callable

from iteris.project import append_jsonl, now_iso, now_stamp, slugify, write_json
from iteris.verification.agent import _upsert_verification_index, verify_agent
from iteris.verification.local import ALLOWED_MODES

DEFAULT_PANEL_RUNS = 2
# Seat launches are staggered so request ids (timestamp-derived) cannot collide.
SEAT_LAUNCH_STAGGER_SECONDS = 0.2


def verify_panel(
    project_root: Path,
    *,
    mode: str,
    claim: str,
    artifacts: list[Path],
    fact_ids: list[str] | None = None,
    target_artifact: Path | None = None,
    runs: int = DEFAULT_PANEL_RUNS,
    executor: str | None = None,
    seat_runner: Callable[..., dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Run ``runs`` independent verification agents in parallel and aggregate.

    The panel passes only if every seat passes (unanimous accept). A seat that
    crashes counts as a rejection — an unverifiable keystone is not a verified
    keystone. ``seat_runner`` exists for tests; production uses ``verify_agent``.
    """
    project_root = project_root.resolve()
    if mode not in ALLOWED_MODES:
        raise ValueError(f"invalid verification mode: {mode}")
    if runs < 1:
        raise ValueError("panel needs at least one run")
    runner = seat_runner or verify_agent
    request_id = f"verify-panel-{now_stamp()}-{slugify(mode + '-' + claim, 40)}"

    def seat(index: int) -> dict[str, Any]:
        time.sleep(index * SEAT_LAUNCH_STAGGER_SECONDS)
        try:
            return runner(
                project_root,
                mode=mode,
                claim=claim,
                artifacts=list(artifacts),
                fact_ids=list(fact_ids or []),
                target_artifact=target_artifact,
                executor=executor,
            )
        except Exception as exc:  # noqa: BLE001 — a dead seat must not kill the panel
            return {"panel_seat_error": str(exc), "passed": False, "verdict": "error"}

    with ThreadPoolExecutor(max_workers=runs) as pool:
        seat_results = list(pool.map(seat, range(runs)))

    _stamp_seat_results(project_root, request_id, seat_results)
    return _aggregate(
        project_root,
        request_id=request_id,
        mode=mode,
        claim=claim,
        target_artifact=target_artifact,
        seat_results=seat_results,
        runs=runs,
    )


def _stamp_seat_results(project_root: Path, request_id: str, seat_results: list[dict[str, Any]]) -> None:
    """Tag each persisted seat result with the panel id.

    Consumers that count verification episodes (rejection streaks, keystone
    verification depth) must treat one panel as one episode, not N; the stamp
    is what lets them roll seats up into the aggregate. The upsert also
    re-writes the index from parsed rows, which heals any line torn by the
    seats' concurrent appends.
    """
    index_path = project_root / "verification" / "VERIFICATION_INDEX.jsonl"
    for item in seat_results:
        seat_id = item.get("request_id")
        if not seat_id or item.get("panel_seat_error"):
            continue
        item["panel_request_id"] = request_id
        result_path = project_root / "verification" / "results" / f"{seat_id}.json"
        if result_path.exists():
            write_json(result_path, item)
        _upsert_verification_index(index_path, item)


def _aggregate(
    project_root: Path,
    *,
    request_id: str,
    mode: str,
    claim: str,
    target_artifact: Path | None,
    seat_results: list[dict[str, Any]],
    runs: int,
) -> dict[str, Any]:
    passed_seats = [item for item in seat_results if item.get("passed")]
    errored_seats = [item for item in seat_results if item.get("panel_seat_error")]
    all_passed = len(passed_seats) == runs
    if all_passed:
        verdict = "accepted"
    elif any(item.get("verdict") in {"rejected", "error"} for item in seat_results):
        verdict = "rejected"
    else:
        verdict = "needs_repair"

    critical_errors: list[dict[str, str]] = []
    gaps: list[dict[str, str]] = []
    checked_artifacts: set[str] = set()
    checked_fact_ids: set[str] = set()
    primary_fact_ids: set[str] = set()
    seats: list[dict[str, Any]] = []
    for item in seat_results:
        seat_id = str(item.get("request_id") or "panel-seat-error")
        seats.append(
            {
                "request_id": item.get("request_id"),
                "verdict": item.get("verdict"),
                "passed": bool(item.get("passed")),
                "summary": item.get("summary") or item.get("panel_seat_error") or "",
            }
        )
        if item.get("panel_seat_error"):
            critical_errors.append({"location": seat_id, "issue": f"panel seat failed to run: {item['panel_seat_error']}"})
            continue
        for entry in item.get("critical_errors") or []:
            critical_errors.append({"location": f"{seat_id}:{entry.get('location', '')}", "issue": str(entry.get("issue", ""))})
        for entry in item.get("gaps") or []:
            gaps.append({"location": f"{seat_id}:{entry.get('location', '')}", "issue": str(entry.get("issue", ""))})
        checked_artifacts.update(str(value) for value in item.get("checked_artifacts") or [])
        checked_fact_ids.update(str(value) for value in item.get("checked_fact_ids") or [])
        primary_fact_ids.update(str(value) for value in item.get("primary_fact_ids") or [])

    result = {
        "schema_version": "iteris.verification_result.v0",
        "request_id": request_id,
        "backend": "panel",
        "mode": mode,
        "claim": claim,
        "verdict": verdict,
        "passed": all_passed,
        "strict_verdict": "correct" if all_passed else "wrong",
        "summary": (
            f"Panel of {runs} independent verification agent(s): {len(passed_seats)} passed, "
            f"{runs - len(passed_seats)} failed{' (' + str(len(errored_seats)) + ' seat error(s))' if errored_seats else ''}. "
            f"Unanimous accept {'reached' if all_passed else 'NOT reached'}."
        ),
        "critical_errors": critical_errors,
        "gaps": gaps,
        "repair_hints": [f"{item['location']}: {item['issue']}" for item in [*critical_errors, *gaps]],
        "checked_artifacts": sorted(checked_artifacts),
        "checked_fact_ids": sorted(checked_fact_ids),
        # Union of the seats' targeted facts (each seat is an agent verification
        # carrying primary_fact_ids); predecessors don't roll up into depth.
        "primary_fact_ids": sorted(primary_fact_ids),
        "target_artifact": str(target_artifact) if target_artifact else None,
        "verification_scope": "agent_panel",
        "claim_ceiling_after_verification": "verified" if all_passed and mode in {"fact", "assembly", "goal_success", "proof"} else ("reviewed" if all_passed else "submitted"),
        "panel_runs": runs,
        "panel_seats": seats,
        "created_at": now_iso(),
        "verifier": "iteris.verification_panel",
    }
    request = {
        "schema_version": "iteris.verification_request.v0",
        "request_id": request_id,
        "backend": "panel",
        "mode": mode,
        "claim": claim,
        "artifacts": sorted(checked_artifacts),
        "fact_ids": sorted(checked_fact_ids),
        "target_artifact": str(target_artifact) if target_artifact else None,
        "panel_runs": runs,
        "seat_request_ids": [seat.get("request_id") for seat in seats],
        "created_at": now_iso(),
    }
    write_json(project_root / "verification" / "requests" / f"{request_id}.json", request)
    write_json(project_root / "verification" / "results" / f"{request_id}.json", result)
    append_jsonl(project_root / "verification" / "VERIFICATION_INDEX.jsonl", result)
    return result

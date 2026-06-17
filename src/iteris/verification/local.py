"""Deterministic structural precheck for verification bundles."""

from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any

from iteris.memory.facts import FACT_REF_RE, validate_fact_file
from iteris.memory.search import load_jsonl
from iteris.project import append_jsonl, now_iso, now_stamp, slugify, write_json

ALLOWED_MODES = {"source", "fact", "assembly", "goal_success", "proof", "experiment", "code", "claim_firewall", "principled_stop"}


def verify_local(
    project_root: Path,
    *,
    mode: str,
    claim: str,
    artifacts: list[Path],
    fact_ids: list[str] | None = None,
    target_artifact: Path | None = None,
) -> dict[str, Any]:
    project_root = project_root.resolve()
    if mode not in ALLOWED_MODES:
        raise ValueError(f"invalid verification mode: {mode}")
    request_id = f"verify-{now_stamp()}-{slugify(mode + '-' + claim, 40)}"
    fact_ids = fact_ids or []
    if target_artifact is not None and target_artifact not in artifacts:
        artifacts = [*artifacts, target_artifact]
    rel_artifacts: list[str] = []
    critical_errors: list[dict[str, str]] = []
    gaps: list[dict[str, str]] = []
    for artifact in artifacts:
        path = artifact if artifact.is_absolute() else project_root / artifact
        if not path.exists():
            critical_errors.append({"location": str(artifact), "issue": "artifact not found"})
        else:
            rel_artifacts.append(_display_path(path, project_root))

    if not claim.strip():
        critical_errors.append({"location": "claim", "issue": "claim is empty"})

    checked_fact_ids: list[str] = []
    if mode == "proof" and not critical_errors:
        proof_text = "\n".join(_artifact_path(rel, project_root).read_text(encoding="utf-8", errors="replace") for rel in rel_artifacts)
        if "## proof" not in proof_text.lower() and "\\begin{proof}" not in proof_text.lower():
            gaps.append({"location": "proof", "issue": "proof artifact does not contain an explicit proof section"})
    elif mode == "experiment" and not critical_errors:
        experiment_text = "\n".join(
            _artifact_path(rel, project_root).read_text(encoding="utf-8", errors="replace") for rel in rel_artifacts
        ).lower()
        # No artifacts at all is the most 0-discriminating case; the empty text
        # falls through to the baseline gap below, matching proof-mode strictness.
        if "baseline" not in experiment_text and "control" not in experiment_text:
            gaps.append(
                {
                    "location": "experiment",
                    "issue": (
                        "experiment artifacts do not declare a baseline/control: state what a trivial or "
                        "random baseline scores on the same instances and what outcome would falsify the claim "
                        "(an experiment any baseline passes is 0-discriminating and is not empirical support)"
                    ),
                }
            )
    elif mode == "fact" and not critical_errors:
        checked_fact_ids = _verify_fact_bundle(project_root, rel_artifacts, fact_ids, critical_errors, gaps)
    elif mode == "assembly" and not critical_errors:
        checked_fact_ids = _verify_assembly(project_root, rel_artifacts, fact_ids, critical_errors, gaps)
    elif mode == "goal_success" and not critical_errors:
        checked_fact_ids = _verify_goal_success(project_root, claim, rel_artifacts, fact_ids, critical_errors, gaps)

    if critical_errors:
        verdict = "rejected"
    elif gaps:
        verdict = "needs_repair"
    else:
        verdict = "accepted"

    result = {
        "schema_version": "iteris.verification_result.v0",
        "request_id": request_id,
        "mode": mode,
        "claim": claim,
        "verdict": verdict,
        "passed": verdict == "accepted",
        "strict_verdict": "correct" if verdict == "accepted" else "wrong",
        "summary": _summary(mode, verdict, claim),
        "critical_errors": critical_errors,
        "gaps": gaps,
        "repair_hints": _repair_hints(verdict, critical_errors, gaps),
        "checked_artifacts": rel_artifacts,
        "checked_fact_ids": checked_fact_ids,
        # The fact(s) this verification actually targeted (the request's explicit
        # fact ids), distinct from predecessors pulled into the bundle. Keystone
        # depth must credit only these, never incidental bundle mentions.
        "primary_fact_ids": [str(f) for f in fact_ids],
        "target_artifact": str(target_artifact) if target_artifact else None,
        "verification_scope": "structural_precheck",
        "claim_ceiling_after_verification": _claim_ceiling(mode, verdict),
        "created_at": now_iso(),
        "verifier": "iteris.structural_precheck",
    }
    request = {
        "schema_version": "iteris.verification_request.v0",
        "request_id": request_id,
        "mode": mode,
        "claim": claim,
        "artifacts": [str(a) for a in artifacts],
        "fact_ids": fact_ids,
        "target_artifact": str(target_artifact) if target_artifact else None,
        "created_at": now_iso(),
    }
    write_json(project_root / "verification" / "requests" / f"{request_id}.json", request)
    write_json(project_root / "verification" / "results" / f"{request_id}.json", result)
    append_jsonl(project_root / "verification" / "VERIFICATION_INDEX.jsonl", result)
    return result


def _summary(mode: str, verdict: str, claim: str) -> str:
    if verdict == "accepted":
        return f"Accepted {mode} claim at structural precheck scope: {claim[:180]}"
    if verdict == "needs_repair":
        return f"{mode} claim needs repair before promotion: {claim[:180]}"
    return f"Rejected {mode} claim due to structural verification errors: {claim[:180]}"


def _display_path(path: Path, project_root: Path) -> str:
    resolved = path.resolve()
    try:
        return str(resolved.relative_to(project_root))
    except ValueError:
        return str(resolved)


def _artifact_path(path_text: str, project_root: Path) -> Path:
    path = Path(path_text)
    return path if path.is_absolute() else project_root / path


def _fact_index(project_root: Path) -> dict[str, dict[str, Any]]:
    rows = load_jsonl(project_root / "memory" / "facts" / "FACT_INDEX.jsonl")
    return {str(row.get("fact_id")): row for row in rows if row.get("fact_id")}


def _verify_fact_bundle(
    project_root: Path,
    rel_artifacts: list[str],
    fact_ids: list[str],
    critical_errors: list[dict[str, str]],
    gaps: list[dict[str, str]],
) -> list[str]:
    index = _fact_index(project_root)
    checked = set(fact_ids)
    fact_artifacts = [rel for rel in rel_artifacts if _artifact_path(rel, project_root).name.startswith("fact-")]
    if not fact_artifacts and not fact_ids:
        gaps.append({"location": "fact", "issue": "fact verification needs a fact artifact or --fact-id"})
    for rel in fact_artifacts:
        path = _artifact_path(rel, project_root)
        result = validate_fact_file(path)
        if not result["ok"]:
            critical_errors.append({"location": rel, "issue": "; ".join(result["errors"])})
            continue
        meta = result["meta"]
        fact_id = str(meta.get("fact_id"))
        checked.add(fact_id)
        if meta.get("status") == "rejected":
            critical_errors.append({"location": fact_id, "issue": "rejected fact cannot pass verification"})
        if meta.get("claim_policy") == "inherited_boundary_advisory":
            gaps.append(
                {
                    "location": fact_id,
                    "issue": "inherited boundary fact requires agent or panel re-verification; structural precheck is insufficient",
                }
            )
        predecessors = meta.get("predecessors") or []
        for pred in predecessors:
            pred_row = index.get(pred)
            if not pred_row:
                critical_errors.append({"location": fact_id, "issue": f"missing predecessor in FACT_INDEX: {pred}"})
            elif pred_row.get("status") != "verified":
                gaps.append({"location": fact_id, "issue": f"predecessor is not verified: {pred}"})
    for fact_id in fact_ids:
        row = index.get(fact_id)
        if not row:
            critical_errors.append({"location": fact_id, "issue": "fact id not found in FACT_INDEX"})
        elif row.get("status") == "rejected":
            critical_errors.append({"location": fact_id, "issue": "rejected fact cannot pass verification"})
        elif row.get("claim_policy") == "inherited_boundary_advisory":
            gaps.append(
                {
                    "location": fact_id,
                    "issue": "inherited boundary fact requires agent or panel re-verification; structural precheck is insufficient",
                }
            )
    return sorted(checked)


def _verify_assembly(
    project_root: Path,
    rel_artifacts: list[str],
    fact_ids: list[str],
    critical_errors: list[dict[str, str]],
    gaps: list[dict[str, str]],
) -> list[str]:
    if not rel_artifacts:
        critical_errors.append({"location": "assembly", "issue": "assembly verification needs a terminal artifact"})
        return []
    target_rel = rel_artifacts[-1]
    target_path = _artifact_path(target_rel, project_root)
    text = target_path.read_text(encoding="utf-8", errors="replace")
    lower = text.lower()
    if "fact index" not in lower:
        gaps.append({"location": target_rel, "issue": "terminal artifact should include a fact index"})
    if "assembly" not in lower:
        gaps.append({"location": target_rel, "issue": "terminal artifact should include an assembly section"})

    referenced = sorted(set(FACT_REF_RE.findall(text)).union(fact_ids))
    if not referenced:
        gaps.append({"location": target_rel, "issue": "terminal artifact must cite at least one fact:<id>"})
        return []

    index = _fact_index(project_root)
    for fact_id in referenced:
        row = index.get(fact_id)
        if not row:
            critical_errors.append({"location": fact_id, "issue": "referenced fact not found in FACT_INDEX"})
            continue
        if row.get("status") != "verified":
            gaps.append({"location": fact_id, "issue": f"referenced fact status is {row.get('status')!r}, not 'verified'"})
        if not row.get("verification"):
            gaps.append({"location": fact_id, "issue": "referenced fact lacks a verification link"})
    return referenced


def _verify_goal_success(
    project_root: Path,
    claim: str,
    rel_artifacts: list[str],
    fact_ids: list[str],
    critical_errors: list[dict[str, str]],
    gaps: list[dict[str, str]],
) -> list[str]:
    checked = _verify_assembly(project_root, rel_artifacts, fact_ids, critical_errors, gaps)
    if critical_errors or not rel_artifacts:
        return checked

    target_rel = rel_artifacts[-1]
    target_path = _artifact_path(target_rel, project_root)
    text = target_path.read_text(encoding="utf-8", errors="replace")
    lower = text.lower()
    claim_lower = claim.lower()

    if "goal summary" not in lower and "original goal" not in lower and "problem summary" not in lower:
        gaps.append({"location": target_rel, "issue": "goal-success verification needs an explicit original goal/problem summary"})

    partial_markers = [
        "answer_type: verified_partial",
        "partial result",
        "remaining bridge",
        "remaining gap",
        "gap remains",
        "does not solve",
        "not solve",
        "not proved",
        "not proven",
        "open gap",
        "future work",
        "blocker",
        "only establishes",
    ]
    allows_partial = any(marker in claim_lower for marker in ["partial", "gap report", "blocker", "impossibility", "counterexample"])
    if not allows_partial:
        for marker in partial_markers:
            if marker in lower:
                gaps.append(
                    {
                        "location": target_rel,
                        "issue": f"terminal artifact appears partial (`{marker}`) but the goal claim does not allow partial completion",
                    }
                )
                break
    return checked


def _claim_ceiling(mode: str, verdict: str) -> str:
    if verdict != "accepted":
        return "submitted"
    if mode in {"fact", "assembly", "goal_success", "proof"}:
        return "verified"
    return "reviewed"


def _repair_hints(verdict: str, critical_errors: list[dict[str, str]], gaps: list[dict[str, str]]) -> list[str]:
    hints: list[str] = []
    if verdict == "accepted":
        return hints
    for item in critical_errors + gaps:
        hints.append(f"{item['location']}: {item['issue']}")
    return hints or ["Provide a stronger evidence bundle and rerun verification."]


def latest_results(project_root: Path) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for path in sorted((project_root / "verification" / "results").glob("verify-*.json")):
        try:
            results.append(json.loads(path.read_text(encoding="utf-8")))
        except Exception:
            continue
    return results


STALE_VERIFICATION_MINUTES = 20.0


def stale_verification_requests(
    project_root: Path,
    *,
    threshold_minutes: float = STALE_VERIFICATION_MINUTES,
    now_timestamp: float | None = None,
) -> list[dict[str, Any]]:
    """Verification requests past the threshold age with no result file.

    A verification agent that dies mid-flight leaves its request in
    ``verification/requests/`` forever with nothing in ``results/`` — observed
    deadlocking a main loop that blocked on the missing result. Age comes from
    the request file's mtime. ``verifier_process_alive`` distinguishes a dead
    verifier (salvage with `verify finalize` or resubmit) from a legitimately
    long run; it is None when the platform cannot answer (no /proc).
    """
    requests_dir = project_root / "verification" / "requests"
    results_dir = project_root / "verification" / "results"
    if not requests_dir.is_dir():
        return []
    reference = time.time() if now_timestamp is None else now_timestamp
    settled_panel_seats = _settled_panel_seat_ids(requests_dir, results_dir)
    stale: list[dict[str, Any]] = []
    for request_path in sorted(requests_dir.glob("*.json")):
        if not request_path.is_file():
            continue
        request_id = request_path.stem
        if (results_dir / f"{request_id}.json").exists():
            continue
        if request_id in settled_panel_seats:
            continue
        try:
            age_minutes = (reference - request_path.stat().st_mtime) / 60.0
        except OSError:
            continue
        if age_minutes < threshold_minutes:
            continue
        stale.append(
            {
                "request_id": request_id,
                "age_minutes": round(age_minutes, 1),
                "verifier_process_alive": _verifier_process_alive(request_id),
                "request_path": str(request_path.relative_to(project_root)),
            }
        )
    stale.sort(key=lambda item: -item["age_minutes"])
    return stale


def _settled_panel_seat_ids(requests_dir: Path, results_dir: Path) -> set[str]:
    """Seat request ids already accounted for by a completed panel.

    A panel seat that crashes never writes its own result file, but the panel
    aggregate counts the crash as a rejection and persists the verdict under
    the panel's own request id. Once that aggregate result exists, the seat's
    leftover request is settled history, not a stale verification — there is
    nothing to finalize or resubmit for it.
    """
    settled: set[str] = set()
    for request_path in requests_dir.glob("*.json"):
        try:
            row = json.loads(request_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(row, dict):
            continue
        seat_ids = row.get("seat_request_ids")
        if not isinstance(seat_ids, list):
            continue
        if not (results_dir / f"{request_path.stem}.json").exists():
            continue
        settled.update(str(seat_id) for seat_id in seat_ids if seat_id)
    return settled


def _verifier_process_alive(request_id: str) -> bool | None:
    """Whether a live process carries this request id in its environment.

    Verification agents run with ITERIS_VERIFICATION_REQUEST_ID set (see
    verification.agent), so /proc environ scanning identifies the exact
    verifier without pid files. Returns None where /proc is unavailable.
    """
    proc_root = Path("/proc")
    if not proc_root.is_dir():
        return None
    # The trailing NUL anchors the match to a full environ entry, so a request
    # id that is a prefix of another live request's id cannot read as alive.
    needle = f"ITERIS_VERIFICATION_REQUEST_ID={request_id}".encode() + b"\x00"
    try:
        candidates = list(proc_root.iterdir())
    except OSError:
        return None
    for pid_dir in candidates:
        if not pid_dir.name.isdigit():
            continue
        try:
            if needle in (pid_dir / "environ").read_bytes():
                return True
        except OSError:
            continue
    return False


_REVISION_TOKEN_RE = re.compile(r"\b(?:rev(?:ision)?|round|v)\s*[\d.]+\b", re.IGNORECASE)
_NON_WORD_RE = re.compile(r"[^a-z0-9\s]+")
_CLAIM_KEY_TOKENS = 12


def claim_streak_key(claim: str) -> str:
    """Normalize a claim so successive revisions of one claim group together.

    Strips revision/round markers, lowercases, and keeps the first few
    significant tokens. Standalone numbers are kept: "Lemma 3" and "Lemma 7"
    are different claims; only revision/round markers denote re-attempts.
    Heuristic by design: it only feeds advisory attention output, never
    gating logic.
    """
    text = _REVISION_TOKEN_RE.sub(" ", claim.lower())
    text = _NON_WORD_RE.sub(" ", text)
    key = " ".join(text.split()[:_CLAIM_KEY_TOKENS])
    return key or claim.lower().strip()


def _error_gap_count(item: dict[str, Any]) -> int:
    """Number of blocking findings (critical errors + gaps) in a result."""
    ce = item.get("critical_errors")
    gaps = item.get("gaps")
    return (len(ce) if isinstance(ce, list) else 0) + (len(gaps) if isinstance(gaps, list) else 0)


def _trend_label(counts: list[int]) -> str:
    """Direction of the error/gap count across a streak (oldest -> newest).

    Distinguishes a CONVERGING repair (findings shrinking — keep going) from a
    STUCK claim (flat/growing — pivot or falsify). Raw streak length cannot
    tell these apart: a 5-rejection chain that converged to acceptance and a
    5-rejection chain that is genuinely stuck both read as "5".
    """
    if len(counts) < 2 or counts[0] == 0:
        return "unknown"
    if counts[-1] < counts[0]:
        return "converging"
    if counts[-1] > counts[0]:
        return "worsening"
    return "flat"


def rejection_streaks(results: list[dict[str, Any]], *, min_streak: int = 3) -> list[dict[str, Any]]:
    """Claims whose latest verification attempts are a run of consecutive rejections.

    Groups fact/proof verification results, orders each group by ``created_at``,
    and reports groups whose trailing run of ``rejected`` verdicts is at least
    ``min_streak`` long. Grouping is union-based: results that share any
    ``checked_fact_id`` are the same streak even when the agent paraphrases the
    claim each round (which used to split a 5-rejection chain into separate
    "worst 3x" reports); the normalized claim key is a fallback for results that
    carry no fact ids. Each streak also carries the error/gap-count TREND so a
    consumer can tell a converging repair from a stuck claim — stop/pivot
    decisions should key on the trend, not the raw count.
    """
    candidates: list[dict[str, Any]] = []
    for item in results:
        if not isinstance(item, dict):
            continue
        if str(item.get("mode") or "") not in {"fact", "proof"}:
            continue
        # Panel seats roll up into their aggregate result; counting them
        # individually would let a single failed panel fake a whole streak.
        if item.get("panel_request_id"):
            continue
        if not str(item.get("claim") or "").strip():
            continue
        candidates.append(item)

    # Union-find over candidates: union any two that share a checked_fact_id or
    # the same normalized claim key, so paraphrase + fact-id drift don't split.
    parent = list(range(len(candidates)))

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a: int, b: int) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[max(ra, rb)] = min(ra, rb)

    by_fact: dict[str, list[int]] = {}
    by_claim: dict[str, list[int]] = {}
    for i, item in enumerate(candidates):
        fact_ids = item.get("checked_fact_ids") or item.get("fact_ids") or []
        for fid in fact_ids:
            by_fact.setdefault(str(fid), []).append(i)
        by_claim.setdefault(claim_streak_key(str(item.get("claim") or "")), []).append(i)
    for bucket in (*by_fact.values(), *by_claim.values()):
        for j in bucket[1:]:
            union(bucket[0], j)

    components: dict[int, list[dict[str, Any]]] = {}
    for i, item in enumerate(candidates):
        components.setdefault(find(i), []).append(item)

    streaks: list[dict[str, Any]] = []
    for items in components.values():
        items.sort(key=lambda entry: str(entry.get("created_at") or ""))
        trailing: list[dict[str, Any]] = []
        for entry in reversed(items):
            if str(entry.get("verdict") or "") == "rejected":
                trailing.append(entry)
            else:
                break
        if len(trailing) < min_streak:
            continue
        trailing.reverse()  # oldest -> newest across the rejected run
        latest = items[-1]
        counts = [_error_gap_count(e) for e in trailing]
        fact_ids = sorted({str(f) for e in items for f in (e.get("checked_fact_ids") or [])})
        streaks.append(
            {
                "claim_key": claim_streak_key(str(latest.get("claim") or "")),
                "claim": str(latest.get("claim") or "")[:300],
                "consecutive_rejections": len(trailing),
                "attempts": len(items),
                "last_request_id": latest.get("request_id"),
                "last_created_at": latest.get("created_at"),
                "checked_fact_ids": fact_ids,
                "grouped_by": "checked_fact_ids" if fact_ids else "claim_key",
                "error_gap_counts": counts,
                "error_gap_trend": _trend_label(counts),
            }
        )
    streaks.sort(key=lambda item: -item["consecutive_rejections"])
    return streaks

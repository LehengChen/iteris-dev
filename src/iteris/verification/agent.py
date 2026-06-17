"""Executor-backed verification agent (codex or claude)."""

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path
from typing import Any

from iteris.codex_logs import (
    CODEX_EVENTS_FILENAME,
    CODEX_LOG_MANIFEST_FILENAME,
    CODEX_STDERR_FILENAME,
    render_codex_events,
    run_codex_exec_json,
)
from iteris.executors import (
    EXECUTOR_CLAUDE,
    build_claude_headless_command,
    build_codex_headless_command,
    headless_home_env,
    resolve_agent_model,
    resolve_executor,
)
from iteris.log_adapters import render_claude_events
from iteris.memory.facts import FACT_REF_RE
from iteris.project import append_jsonl, now_iso, now_stamp, slugify, write_json
from iteris.verification.local import ALLOWED_MODES


DEFAULT_MODEL = os.getenv("ITERIS_VERIFICATION_MODEL", os.getenv("CODEX_MODEL", "gpt-5.5"))
DEFAULT_REASONING_EFFORT = os.getenv("ITERIS_VERIFICATION_REASONING_EFFORT", "xhigh")
DEFAULT_TIMEOUT_SECONDS = int(os.getenv("ITERIS_VERIFICATION_TIMEOUT_SECONDS", "3600"))


def resolve_verification_executor(executor: str | None = None, *, env: dict[str, str] | None = None) -> str:
    """Verifier executor: explicit > $ITERIS_VERIFICATION_EXECUTOR > $ITERIS_EXECUTOR > codex.

    Independent from the solver so a run can verify with a different model than
    it solves with — cross-model verification reduces correlated errors. When
    neither verification-specific override is set, it falls back to the main
    executor via resolve_executor.
    """
    source_env = os.environ if env is None else env
    return resolve_executor(executor or source_env.get("ITERIS_VERIFICATION_EXECUTOR"), env=source_env)


def verify_agent(
    project_root: Path,
    *,
    mode: str,
    claim: str,
    artifacts: list[Path],
    fact_ids: list[str] | None = None,
    target_artifact: Path | None = None,
    executor: str | None = None,
    executable: str | None = None,
    model: str | None = None,
    reasoning_effort: str | None = None,
    timeout_seconds: int | None = None,
) -> dict[str, Any]:
    """Run an independent verification agent (codex or claude) and persist its result."""
    project_root = project_root.resolve()
    if mode not in ALLOWED_MODES:
        raise ValueError(f"invalid verification mode: {mode}")
    executor_name = resolve_verification_executor(executor)
    agent_bin = executable or shutil.which(executor_name) or executor_name
    if shutil.which(agent_bin) is None and not Path(agent_bin).exists():
        raise RuntimeError(f"{executor_name} executable is not installed; real verification cannot run")

    fact_ids = fact_ids or []
    if target_artifact is not None and target_artifact not in artifacts:
        artifacts = [*artifacts, target_artifact]

    request_id = f"verify-{now_stamp()}-{slugify(mode + '-' + claim, 40)}"
    rel_artifacts = [_display_path(artifact, project_root) for artifact in artifacts]
    run_dir = project_root / "verification" / "agent_runs" / request_id
    request = {
        "schema_version": "iteris.verification_request.v0",
        "request_id": request_id,
        "backend": "agent",
        "executor": executor_name,
        "mode": mode,
        "claim": claim,
        "artifacts": rel_artifacts,
        "fact_ids": fact_ids,
        "target_artifact": str(target_artifact) if target_artifact else None,
        "created_at": now_iso(),
        "prompt_path": str((run_dir / "prompt.md").relative_to(project_root)),
        "codex_log": str((run_dir / "codex.log").relative_to(project_root)),
        "codex_events": str((run_dir / CODEX_EVENTS_FILENAME).relative_to(project_root)),
        "codex_stderr": str((run_dir / CODEX_STDERR_FILENAME).relative_to(project_root)),
        "codex_log_manifest": str((run_dir / CODEX_LOG_MANIFEST_FILENAME).relative_to(project_root)),
    }
    write_json(project_root / "verification" / "requests" / f"{request_id}.json", request)

    run_dir.mkdir(parents=True, exist_ok=True)
    write_json(run_dir / "request.json", request)
    prompt = build_agent_prompt(request_id=request_id, request_path=run_dir / "request.json", output_path=run_dir / "verification.json")
    (run_dir / "prompt.md").write_text(prompt, encoding="utf-8")

    if executor_name == EXECUTOR_CLAUDE:
        cmd = build_claude_headless_command(
            project_root=project_root,
            executable=agent_bin,
            model=resolve_agent_model(executor_name, model, kind="verification"),
        )
        render_fn = render_claude_events
    else:
        cmd = build_codex_headless_command(
            project_root=project_root,
            executable=agent_bin,
            model=model or DEFAULT_MODEL,
            reasoning_effort=reasoning_effort or DEFAULT_REASONING_EFFORT,
        )
        render_fn = render_codex_events
    log_manifest = run_codex_exec_json(
        project_root=project_root,
        run_dir=run_dir,
        process_kind="verification_agent",
        run_id=request_id,
        command=cmd,
        prompt=prompt,
        prompt_path=run_dir / "prompt.md",
        executor=executor_name,
        render_fn=render_fn,
        log_adapter=executor_name,
        env_updates={
            "ITERIS_PROCESS_ROLE": "verification_agent",
            "ITERIS_VERIFICATION_REQUEST_ID": request_id,
            "ITERIS_PROJECT_ROOT": str(project_root),
            "ITERIS_EXECUTOR": executor_name,
            **headless_home_env(executor_name),
        },
        timeout_seconds=timeout_seconds if timeout_seconds is not None else DEFAULT_TIMEOUT_SECONDS,
    )

    log_path = run_dir / "codex.log"
    if log_manifest.get("timed_out"):
        raise RuntimeError(f"verification agent timed out; see {log_path}")
    if log_manifest.get("returncode") != 0:
        raise RuntimeError(f"verification agent failed with exit code {log_manifest.get('returncode')}; see {log_path}")

    output_path = run_dir / "verification.json"
    if not output_path.exists():
        raise RuntimeError(f"verification agent did not write {output_path}; see {log_path}")
    payload = json.loads(output_path.read_text(encoding="utf-8"))
    result = normalize_agent_output(
        request=request,
        payload=payload,
        run_dir=run_dir,
        log_path=log_path,
    )
    write_json(project_root / "verification" / "results" / f"{request_id}.json", result)
    append_jsonl(project_root / "verification" / "VERIFICATION_INDEX.jsonl", result)
    return result


def finalize_agent_run(project_root: Path, *, request_id: str, overwrite: bool = False) -> dict[str, Any]:
    """Normalize a completed verification agent run after an interrupted submit."""
    project_root = project_root.resolve()
    run_dir = project_root / "verification" / "agent_runs" / request_id
    if not run_dir.exists():
        raise FileNotFoundError(f"agent run not found: {run_dir}")

    result_path = project_root / "verification" / "results" / f"{request_id}.json"
    if result_path.exists() and not overwrite:
        result = json.loads(result_path.read_text(encoding="utf-8"))
        _upsert_verification_index(project_root / "verification" / "VERIFICATION_INDEX.jsonl", result)
        return result

    request_path = run_dir / "request.json"
    if not request_path.exists():
        request_path = project_root / "verification" / "requests" / f"{request_id}.json"
    if not request_path.exists():
        raise FileNotFoundError(f"verification request not found for {request_id}")

    output_path = run_dir / "verification.json"
    if not output_path.exists():
        raise FileNotFoundError(f"agent verification output not found: {output_path}")

    request = json.loads(request_path.read_text(encoding="utf-8"))
    payload = json.loads(output_path.read_text(encoding="utf-8"))
    result = normalize_agent_output(
        request=request,
        payload=payload,
        run_dir=run_dir,
        log_path=run_dir / "codex.log",
    )
    write_json(result_path, result)
    _upsert_verification_index(project_root / "verification" / "VERIFICATION_INDEX.jsonl", result)
    return result


def build_codex_command(
    *,
    project_root: Path,
    prompt: str,
    executable: str,
    model: str,
    reasoning_effort: str,
) -> list[str]:
    # Back-compat shim; the canonical builder now lives in iteris.executors.
    # ``prompt`` is accepted for signature parity but delivered on stdin.
    del prompt
    return build_codex_headless_command(
        project_root=project_root,
        executable=executable,
        model=model,
        reasoning_effort=reasoning_effort,
    )


def build_agent_prompt(*, request_id: str, request_path: Path, output_path: Path) -> str:
    return f"""You are the Iteris Verification Agent.

Read the verification request at `{request_path}` and verify it inside this
project workspace. Do not treat schema validity as enough. Your job is to check
whether the claim is actually supported by the cited artifacts, facts, proof
steps, source material, and external references when relevant.

For mathematical work:
- verify statements and subproofs sequentially in the order written;
- check assumptions, theorem applications, definitions, and unjustified jumps;
- if an external theorem is cited, search/check the reference before accepting
  it, and verify that the cited theorem's hypotheses and terminology match this
  problem;
- a load-bearing external result must be located to a specific checkable
  source (paper or identifier plus the precise statement); reliance on an
  unnamed "standard theorem" or "well-known result" that you cannot locate
  and check is a critical error, not a gap;
- mark any logical error, missing argument, unsupported theorem application, or
  mismatched reference as a failure.

Mode guidance:
- `fact`: verify the fact statement, the cited evidence, and predecessor facts.
  Passing requires that the fact is true within its stated claim scope, not just
  well-formed.
- `assembly`: verify that the terminal artifact answers the goal only by using
  cited verified facts, and that those facts actually support the assembled
  conclusion. If the artifact claims a theorem that the facts do not prove,
  return `wrong`. An artifact that claims to resolve a broader or universally
  quantified goal but whose cited facts only establish a single instance,
  sub-family, or degenerate/boundary case overclaims and is `wrong`.
- `goal_success`: verify that the terminal artifact satisfies the original
  user goal in the request claim, not merely that the artifact is internally
  assembled from verified facts. A verified partial result, blocker, remaining
  bridge, gap report, or weaker theorem is `wrong` unless the request claim
  explicitly asks for that limited output.
  Scope adequacy and quantifier check (mandatory): before judging, restate the
  goal's quantifier structure and its intended non-trivial regime. If the goal
  is universally quantified — "for ANY/EVERY choice of X achieve P", "an
  algorithm that works for all inputs/instances" — then an artifact that
  establishes P only for a single instance, a hand-picked sub-family, or a
  boundary case is `wrong`, even when that sub-result is itself correct and
  verified, unless it genuinely covers the whole quantifier or the problem
  provably reduces to that case. The solver does NOT get to pick the easiest
  admissible instance and declare the goal solved when the goal quantifies over
  all of them. Also REJECT degenerate instantiations that trivialize the
  objective: a parameter regime where the quantity to be improved collapses to a
  trivial value (an approximation/competitive factor that becomes 1, a bound
  that becomes vacuous), an "improvement" measured against an inflated or padded
  nominal quantity rather than the effective/tight one for that instance, or
  strictness manufactured only at a boundary (e.g. an empty or zero-optimum
  input). Such a single-instance or degenerate obstruction may be recorded as an
  intermediate fact but is `wrong` as goal_success. A genuinely scoped answer is
  full goal_success when the SOURCE goal as authored (not the solver) defines that
  scope as the target — a source that explicitly restricts to a named sub-case is
  satisfied by solving that sub-case. Verify coverage against the ORIGINAL SOURCE goal (found in the cited
  `source_problem` fact or the referenced direction file's `## Success criteria` /
  `## What does NOT count`), not the solver-authored request claim: the claim is
  written by the solver and may
  quietly NARROW the goal to a self-chosen sub-model, scope, or parameter regime.
  If the claim or artifact restricts success to a self-selected sub-scope of the
  source goal, that is a scoped PARTIAL and is `wrong` as goal_success even though
  the claim "asks for" that narrow scope — the solver does not get to redefine
  success by narrowing the claim. Honor any explicit anti-narrowing,
  anti-degeneracy, or "what does NOT count" instruction in the source. If the SOURCE goal's
  deliverable is itself a maximality/optimality claim — it asks to PROVE a quantity
  is OPTIMAL / SHARP / TIGHT / BEST-POSSIBLE, not merely to ACHIEVE a target bound,
  give an algorithm, or prove a stated bound suffices — then `correct` requires
  BOTH a verified constructive upper bound AND a verified matching lower bound,
  proven in the SAME resource/access model and the intended non-degenerate regime;
  an achievability/upper bound alone, or a bound obtained in a different or
  trivializing model (e.g. retreating to an exact-cost model so the optimum
  collapses to a constant), does NOT prove optimality. A goal that asks only for an
  algorithm meeting a stated bound, or for a one-sided bound, does NOT trigger the
  matching-lower-bound requirement — demanding a lower bound there is
  over-rejection. When optimality IS the deliverable but the matching lower bound
  is missing, that is `wrong` as full goal_success — record the gap as "achievable
  bound; optimal/sharp OPEN" and finalize the honest partial via principled_stop
  (the research-open landing below), not endless goal_success retries. Adopt a refuting stance: look for the self-narrowing,
  the model-shift, the missing required matching bound, and the coverage gap
  before accepting, but reserve `wrong` for a CONCRETE identified gap (a named
  uncovered class, a witnessed model-shift, a missing required matching bound): if
  the artifact clearly covers the source goal's full quantifier structure, accept
  it; if the source goal cannot be located among the cited artifacts, report THAT
  as the gap rather than refuting a complete-looking answer on mere uncertainty.
  When the goal or the direction
  source file it references contains a `## Success criteria` section, check
  the artifact against EVERY criterion; a result named under `## What does
  NOT count` does not satisfy the goal even if itself verified. When the goal
  declares an audit contract (`## Audit routes`), every enumerated route must
  carry an explicit verdict — a proof or a precisely-witnessed obstruction —
  plus a synthesis verdict; a single positive result with unaddressed routes
  is `wrong`.
- `principled_stop`: the request claim is the ORIGINAL goal; the artifact asks to
  STOP because the full goal is unreachable AS STATED, not because it is solved.
  This is the honest terminal for "provably impossible as stated" or "reduced to
  a named open subproblem" — it is NOT a license to give up on hard work. Restate
  the goal's quantifier structure first (as for goal_success), then default to
  `wrong` and return `correct` ONLY if ALL hold: (1) the artifact cites a VERIFIED
  obstruction — either a verified impossibility result for the FULL goal exactly
  as stated, or a precise reduction to a named, well-characterized open
  subproblem the goal provably reduces to; a vague "this is hard", an unproven
  conjecture, or an unverified lower bound is `wrong`; (2) the obstruction is on
  the genuine full goal, not a strawman, a misformalization, or a
  degenerate-instance escape (the mirror of the goal_success degeneracy check: an
  "impossibility" manufactured by misreading the goal, padding/inflating a bound,
  or picking an adversarial sub-instance is `wrong`); (3) the artifact also states
  the STRONGEST valid result actually achieved AND verified (e.g. the
  conditional/partial theorem), so the stop captures real progress rather than a
  blank give-up; (4) there is evidence of genuine exploration (recorded rejected
  routes / alternative attempts) — a premature stop with obvious unexplored
  routes is `wrong`. For a research-OPEN goal (not provably impossible, but not
  fully closable this run), a precise, VERIFIED reduction (the full goal provably
  follows from the named subproblem; reduction direction and faithfulness checked)
  of the full goal to a named, well-characterized OPEN subproblem PLUS a
  comprehensive obstruction map of the
  standard routes, accompanied by the strongest verified partial and genuine
  exploration evidence, IS a legitimate principled stop under (1)'s reduction
  branch — the honest scientific terminal for an open problem. Do not reject such
  an honest strong-partial merely because the full goal remains open; reject only
  a cop-out (a weak partial, obvious unexplored routes, a vague "this is hard", or
  an overclaim).
- `proof`: verify the full proof text in paper order. A proof with any gap is
  `wrong`.
- `experiment`: verify the claim against the cited artifacts as for other
  modes, and additionally verify discriminating power. The artifact must
  declare its baseline/control and what outcome would falsify the claim; check
  whether a trivial baseline would also pass on the same instances. An
  experiment that any baseline passes is 0-discriminating and must not pass as
  empirical support for the claim. When the claim selects between competing
  hypotheses (e.g. growth laws), also check that the ANALYSIS METHOD can
  distinguish them on the data range: near-collinear fits compared by goodness
  of fit have no discriminating power — demand a diagnostic that separates the
  hypotheses (such as increment/difference behavior) or mark the selection as
  a gap. Check that the experiment declares its scope (what the data is a
  proxy for) and does not over-claim beyond it.
- `source`, `code`, `claim_firewall`: verify the claim against
  the cited artifacts at the appropriate scope and report all gaps.

Write exactly one JSON object to `{output_path}` with this shape:

```json
{{
  "verification_report": {{
    "summary": "string",
    "critical_errors": [
      {{"location": "string", "issue": "string"}}
    ],
    "gaps": [
      {{"location": "string", "issue": "string"}}
    ]
  }},
  "verdict": "correct",
  "repair_hints": "string",
  "checked_artifacts": ["path"],
  "checked_fact_ids": ["fact:id"]
}}
```

Use `verdict: "correct"` if and only if there are no critical errors and no
gaps. Otherwise use `verdict: "wrong"` and provide concrete repair hints.

Request id: {request_id}
"""


def normalize_agent_output(*, request: dict[str, Any], payload: dict[str, Any], run_dir: Path, log_path: Path) -> dict[str, Any]:
    # Older requests (pre multi-executor) carry no executor field → codex.
    executor_name = str(request.get("executor") or "codex")
    report = payload.get("verification_report") if isinstance(payload.get("verification_report"), dict) else {}
    critical_errors = _list_of_dicts(report.get("critical_errors"))
    gaps = _list_of_dicts(report.get("gaps"))
    strict = payload.get("verdict")
    passed = strict == "correct" and not critical_errors and not gaps
    verdict = "accepted" if passed else ("rejected" if critical_errors else "needs_repair")
    checked_artifacts = [str(item) for item in payload.get("checked_artifacts") or request.get("artifacts") or []]
    checked_fact_ids = [str(item) for item in payload.get("checked_fact_ids") or _fact_ids_from_artifacts(run_dir.parent.parent.parent, checked_artifacts)]
    result = {
        "schema_version": "iteris.verification_result.v0",
        "request_id": request["request_id"],
        "backend": "agent",
        "mode": request["mode"],
        "claim": request["claim"],
        "verdict": verdict,
        "passed": passed,
        "strict_verdict": "correct" if passed else "wrong",
        "summary": str(report.get("summary") or payload.get("summary") or ""),
        "critical_errors": critical_errors,
        "gaps": gaps,
        "repair_hints": payload.get("repair_hints") or _default_repair_hints(critical_errors, gaps),
        "checked_artifacts": checked_artifacts,
        "checked_fact_ids": checked_fact_ids,
        # The fact(s) the request asked to verify, distinct from the (agent-
        # reported, unordered) checked_fact_ids that also list predecessors.
        "primary_fact_ids": [str(item) for item in request.get("fact_ids") or []],
        "target_artifact": request.get("target_artifact"),
        "executor": executor_name,
        "verification_scope": f"{executor_name}_agent",
        "claim_ceiling_after_verification": _claim_ceiling(request["mode"], passed),
        "agent_run_dir": str(run_dir.relative_to(run_dir.parent.parent.parent)),
        "agent_log": str(log_path.relative_to(run_dir.parent.parent.parent)),
        "agent_events_log": _optional_rel(run_dir / CODEX_EVENTS_FILENAME, run_dir.parent.parent.parent),
        "agent_stderr_log": _optional_rel(run_dir / CODEX_STDERR_FILENAME, run_dir.parent.parent.parent),
        "agent_log_manifest": _optional_rel(run_dir / CODEX_LOG_MANIFEST_FILENAME, run_dir.parent.parent.parent),
        "created_at": now_iso(),
        "verifier": f"iteris.{executor_name}_verification_agent",
    }
    return result


def _display_path(path: Path, project_root: Path) -> str:
    resolved = path if path.is_absolute() else project_root / path
    if not resolved.exists():
        return str(path)
    try:
        return str(resolved.resolve().relative_to(project_root))
    except ValueError:
        return str(resolved.resolve())


def _list_of_dicts(value: Any) -> list[dict[str, str]]:
    if not isinstance(value, list):
        return []
    out: list[dict[str, str]] = []
    for item in value:
        if isinstance(item, dict):
            out.append({"location": str(item.get("location", "")), "issue": str(item.get("issue", ""))})
    return out


def _default_repair_hints(critical_errors: list[dict[str, str]], gaps: list[dict[str, str]]) -> str:
    if not critical_errors and not gaps:
        return ""
    return "\n".join(f"{item['location']}: {item['issue']}" for item in [*critical_errors, *gaps])


def _claim_ceiling(mode: str, passed: bool) -> str:
    if not passed:
        return "submitted"
    if mode == "principled_stop":
        # A certified principled stop is a weaker terminal than a solved goal:
        # the full goal was NOT achieved, only certified unreachable-as-stated /
        # reduced. Keep it distinct from "verified" so downstream tools never
        # treat it as a solved result.
        return "reduced"
    if mode in {"fact", "assembly", "goal_success", "proof"}:
        return "verified"
    return "reviewed"


def _fact_ids_from_artifacts(project_root: Path, artifacts: list[str]) -> list[str]:
    ids: set[str] = set()
    for artifact in artifacts:
        path = Path(artifact)
        resolved = path if path.is_absolute() else project_root / path
        if not resolved.exists() or resolved.is_dir():
            continue
        ids.update(FACT_REF_RE.findall(resolved.read_text(encoding="utf-8", errors="replace")))
    return sorted(ids)


def _optional_rel(path: Path, root: Path) -> str | None:
    if not path.exists():
        return None
    try:
        return str(path.resolve().relative_to(root))
    except ValueError:
        return str(path.resolve())


def _upsert_verification_index(path: Path, result: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    request_id = result.get("request_id")
    rows: list[dict[str, Any]] = []
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if row.get("request_id") != request_id:
                rows.append(row)
    rows.append(result)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")

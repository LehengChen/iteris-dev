"""Terminal finalize report + verification-status emission and STATUS stamping."""

from __future__ import annotations

import re

from iteris.gitops import status as git_status
from iteris.project import now_iso, read_json, write_json
from pathlib import Path
from iteris.commands.goal.targets import reduced_artifact_for, verified_artifact_for


def build_goal_finalize_report(
    root: Path,
    *,
    target_artifact: str,
    require_clean: bool = True,
    terminal_mode: str = "goal_success",
) -> dict[str, object]:
    # terminal_mode selects the completion gate: "goal_success" (the full goal is
    # solved) or "principled_stop" (the full goal is certified
    # unreachable-as-stated / reduced to an open subproblem). Both still require a
    # passed assembly + a clean worktree.
    target_path = root / target_artifact
    assembly = _latest_passed_verification(root, mode="assembly", target_artifact=target_artifact)
    terminal = _latest_passed_verification(root, mode=terminal_mode, target_artifact=target_artifact)
    git = git_status(root)
    clean_ok = (git.get("repo") is True and git.get("dirty") is False) if require_clean else True
    checks = [
        {"name": "target_artifact_exists", "ok": target_path.exists(), "detail": target_artifact},
        {"name": "assembly_verification_passed", "ok": assembly is not None, "detail": assembly.get("request_id") if assembly else None},
        {"name": f"{terminal_mode}_verification_passed", "ok": terminal is not None, "detail": terminal.get("request_id") if terminal else None},
        {"name": "git_worktree_clean", "ok": clean_ok, "detail": git.get("short") if require_clean else "not required"},
    ]
    return {
        "schema_version": "iteris.goal_finalization.v0",
        "project_path": str(root),
        "target_artifact": target_artifact,
        "terminal_mode": terminal_mode,
        "ok": all(bool(check["ok"]) for check in checks),
        "checks": checks,
        "assembly_request_id": assembly.get("request_id") if assembly else None,
        f"{terminal_mode}_request_id": terminal.get("request_id") if terminal else None,
        "git": git,
    }


def _latest_passed_verification(root: Path, *, mode: str, target_artifact: str) -> dict[str, object] | None:
    results_dir = root / "verification" / "results"
    if not results_dir.exists():
        return None
    matches = sorted(results_dir.glob("*.json"), key=lambda path: path.stat().st_mtime, reverse=True)
    for path in matches:
        payload = read_json(path, default=None)
        if not isinstance(payload, dict):
            continue
        if payload.get("mode") != mode:
            continue
        if payload.get("passed") is not True:
            continue
        if not _verification_mentions_target(payload, target_artifact):
            continue
        return payload
    return None


def _verification_mentions_target(payload: dict[str, object], target_artifact: str) -> bool:
    if payload.get("target_artifact") == target_artifact:
        return True
    for key in ["checked_artifacts", "artifacts"]:
        values = payload.get(key)
        if isinstance(values, list) and target_artifact in [str(value) for value in values]:
            return True
    return False


def _stamp_status_last_updated(text: str, stamp: str) -> str:
    """Set/refresh the STATUS.md `last_updated:` header to ``stamp``.

    A CLI-side STATUS write that bumps `phase:` but leaves a stale
    `last_updated:` misleads a reader trusting the header (observed lagging the
    real edit by ~1h). Update it in place, or insert it after `phase:` when
    absent.
    """
    if re.search(r"(?m)^last_updated:\s*.*$", text):
        return re.sub(r"(?m)^last_updated:\s*.*$", f"last_updated: {stamp}", text, count=1)
    if re.search(r"(?m)^phase:\s*.*$", text):
        return re.sub(r"(?m)^(phase:\s*.*)$", rf"\1\nlast_updated: {stamp}", text, count=1)
    return f"last_updated: {stamp}\n{text}"


def _stamp_status_phase(root: Path, phase: str) -> bool:
    """Pin STATUS.md `phase:` to the contract vocabulary (and refresh
    `last_updated:`). Returns True when the file changed. Free-form worker
    phases ("verified", "complete", ...) are not machine-readable; supervisors
    key on `goal_success_verified`."""
    status_path = root / "STATUS.md"
    try:
        text = status_path.read_text(encoding="utf-8") if status_path.exists() else ""
    except OSError:
        return False
    if re.search(rf"(?m)^phase:\s*{re.escape(phase)}\s*$", text):
        return False
    new_text, count = re.subn(r"(?m)^phase:\s*.*$", f"phase: {phase}", text, count=1)
    if count == 0:
        new_text = f"phase: {phase}\n{text}"
    new_text = _stamp_status_last_updated(new_text, now_iso())
    try:
        status_path.write_text(new_text, encoding="utf-8")
    except OSError:
        return False
    return True


def _emit_verification_status(
    root: Path,
    *,
    target_artifact: str,
    report: dict[str, object],
) -> str | None:
    """Record an authoritative verification status next to the answer.

    Always writes ``<results-dir>/VERIFICATION_STATUS.json`` (truth, pass or
    fail). When the finalize gate passes, also writes the verified-named copy
    of the answer with a stamped header, so a file named ``answer_verified.md``
    exists IFF goal-success verification actually passed. Returns the verified
    artifact path when emitted, else None.
    """
    target_path = root / target_artifact
    ok = bool(report.get("ok"))
    terminal_mode = str(report.get("terminal_mode") or "goal_success")
    terminal_request_id = report.get(f"{terminal_mode}_request_id")
    status = {
        "schema_version": "iteris.verification_status.v0",
        "target_artifact": target_artifact,
        "terminal_mode": terminal_mode,
        "ok": ok,
        "assembly_request_id": report.get("assembly_request_id"),
        f"{terminal_mode}_request_id": terminal_request_id,
        "verified_artifact": None,
    }
    verified_rel: str | None = None
    if ok and target_path.exists():
        if terminal_mode == "principled_stop":
            verified_rel = reduced_artifact_for(target_artifact)
            header = (
                f"<!-- ITERIS PRINCIPLED-STOP CERTIFIED: principled_stop {terminal_request_id} "
                f"assembly {report.get('assembly_request_id')} -->\n"
            )
        else:
            verified_rel = verified_artifact_for(target_artifact)
            header = (
                f"<!-- ITERIS VERIFIED: goal_success {terminal_request_id} "
                f"assembly {report.get('assembly_request_id')} -->\n"
            )
        verified_path = root / verified_rel
        try:
            body = target_path.read_text(encoding="utf-8")
        except OSError:
            body = ""
        if verified_path.resolve() == target_path.resolve():
            # Legacy project pinned answer_verified.md directly: stamp in place
            # without duplicating the header on repeat finalize calls.
            if not body.lstrip().startswith("<!-- ITERIS "):
                try:
                    target_path.write_text(header + body, encoding="utf-8")
                except OSError:
                    pass
        else:
            verified_path.parent.mkdir(parents=True, exist_ok=True)
            try:
                verified_path.write_text(header + body, encoding="utf-8")
            except OSError:
                verified_rel = None
        status["verified_artifact"] = verified_rel
    # Co-locate the status file with the answer (results/<id>/), falling back to
    # the project root when the target has no parent directory. Only rewrite
    # when the content changes: finalize runs on every loop iteration including
    # failing ones, and a needless rewrite would re-dirty the worktree and keep
    # a later --require-clean finalize from ever passing on its own status file.
    status_dir = target_path.parent if target_path.parent != root else root
    status_dir.mkdir(parents=True, exist_ok=True)
    status_path = status_dir / "VERIFICATION_STATUS.json"
    if read_json(status_path, default=None) != status:
        write_json(status_path, status)
    return verified_rel

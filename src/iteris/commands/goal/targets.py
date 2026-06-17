"""Problem-id and terminal-artifact resolution for the goal command.

Pure path/string helpers plus the run's target-artifact lookup. Kept separate so
the verified/reduced artifact naming rules (the truthful "solved" vs "principled
stop" signals) live in one place.
"""

from __future__ import annotations

from pathlib import Path, PurePosixPath

from iteris.project import read_json, slugify


def default_problem_id(root: Path) -> str:
    return slugify(root.name, 50) or "project"


def default_target_artifact(problem_id: str) -> str:
    # The working terminal artifact is verification-neutral on purpose: a file
    # literally named answer_verified.md must not exist until goal-success
    # verification actually passes (otherwise results/ shows a "verified"
    # answer for a run whose goal-success was rejected). finalize() emits the
    # verified-named copy only when the gate passes; see verified_artifact_for.
    safe_problem_id = slugify(problem_id, 80) or "project"
    return f"results/{safe_problem_id}/answer.md"


def verified_artifact_for(target_artifact: str) -> str:
    """Path of the post-verification copy of ``target_artifact``.

    ``results/<id>/answer.md`` -> ``results/<id>/answer_verified.md``. Any other
    target keeps its stem and gains a ``_verified`` suffix. finalize() writes
    this only when goal-success verification passes, so its presence is a
    truthful signal that downstream tools (generalize/evolve) can rely on.
    A target that already ends in ``_verified`` (legacy projects that pinned
    answer_verified.md directly) is its own verified artifact, stamped in place.
    """
    p = PurePosixPath(target_artifact)
    if p.stem.endswith("_verified"):
        return target_artifact
    return str(p.with_name(f"{p.stem}_verified{p.suffix}"))


def reduced_artifact_for(target_artifact: str) -> str:
    """Path of the post-certification copy for a PRINCIPLED-STOP terminal.

    ``results/<id>/answer.md`` -> ``results/<id>/answer_reduced_verified.md``.
    Deliberately distinct from ``verified_artifact_for`` so that
    ``answer_verified.md`` stays a truthful "goal solved" signal: a principled
    stop (a certified impossibility-as-stated or reduction-to-open-subproblem) is
    a real but WEAKER terminal, and downstream tools (generalize/evolve) must not
    mistake it for a solved result. finalize() writes this only when a
    ``principled_stop`` verification passes.
    """
    p = PurePosixPath(target_artifact)
    stem = p.stem
    if stem.endswith("_reduced_verified"):
        return target_artifact
    if stem.endswith("_verified"):
        stem = stem[: -len("_verified")]
    return str(p.with_name(f"{stem}_reduced_verified{p.suffix}"))


def project_target_artifact(root: Path) -> str | None:
    state = read_json(root / ".iteris" / "current_run.json", default={})
    if isinstance(state, dict) and isinstance(state.get("target_artifact"), str) and state["target_artifact"].strip():
        return str(state["target_artifact"]).strip()

    status_path = root / "STATUS.md"
    if not status_path.exists():
        return None
    for line in status_path.read_text(encoding="utf-8", errors="replace").splitlines():
        key, sep, value = line.partition(":")
        if sep and key.strip() == "target_artifact" and value.strip():
            return value.strip()
    return None


def resolve_goal_defaults(
    root: Path,
    *,
    problem_id: str | None = None,
    target_artifact: str | None = None,
) -> tuple[str, str]:
    resolved_problem_id = problem_id or default_problem_id(root)
    resolved_target = target_artifact or (None if problem_id else project_target_artifact(root)) or default_target_artifact(resolved_problem_id)
    return resolved_problem_id, resolved_target

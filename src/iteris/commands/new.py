"""Project creation command for the public Iteris workflow."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import typer

from iteris import log
from iteris.bootstrap import run_once
from iteris.commands.goal import resolve_goal_defaults
from iteris.events import record_event
from iteris.gitops import GitError, checkpoint, init_git
from iteris.inherit import inherit_boundary
from iteris.project import init_project, is_project, now_iso, source_file
from iteris.references import import_references


def perform_new_project(
    root: Path,
    *,
    source: Path,
    references: list[Path] | None = None,
    inherit_frontier: Path | None = None,
    allow_non_empty: bool = False,
    force: bool = False,
) -> dict[str, Any]:
    """Create an Iteris project programmatically (shared by CLI and monitor wizard)."""
    root = root.resolve()
    reference_paths = list(references or [])
    for path in reference_paths:
        if not path.expanduser().exists():
            raise typer.BadParameter(f"references path not found: {path}")
    inherit_root = inherit_frontier.resolve() if inherit_frontier else None
    if inherit_root is not None and not is_project(inherit_root):
        raise typer.BadParameter(f"--inherit-frontier is not an Iteris project: {inherit_root}")

    if is_project(root) and not force:
        if reference_paths or inherit_root:
            raise typer.BadParameter(
                "this directory is already an Iteris project; use `iteris tool frontier inherit . --from <parent>` "
                "for boundary inheritance and copy reference material into references/user/ directly"
            )
        payload = _existing_project_payload(root)
        payload["already_exists"] = True
        return payload

    source_path = source.resolve()
    if not source_path.exists():
        raise typer.BadParameter(f"source file not found: {source_path}")

    preexisting_files = _preexisting_files(root) if root.exists() and not is_project(root) else []
    if preexisting_files and not allow_non_empty:
        raise typer.BadParameter(
            f"{root} is not empty and is not an Iteris project. "
            "Use an empty directory, or pass --allow-non-empty if this is intentional."
        )

    init_result = init_project(root, source=source_path, force=force)
    copied_source = source_file(root)
    source_rel = str(copied_source.relative_to(root)) if copied_source else None
    references_result = import_references(root, reference_paths) if reference_paths else None
    problem_id, target_artifact = resolve_goal_defaults(root)
    target_path = root / target_artifact
    target_path.parent.mkdir(parents=True, exist_ok=True)
    bootstrap_result = run_once(root)
    inherit_result = inherit_boundary(root, inherit_root) if inherit_root is not None else None
    _write_status(root, target_artifact=target_artifact)
    try:
        git_init = init_git(root)
        checkpoint_result = checkpoint(
            root,
            message="checkpoint: initialize Iteris project",
            paths=_initial_checkpoint_paths(root, source_rel=source_rel, bootstrap_result=bootstrap_result),
        )
    except GitError as exc:
        raise typer.BadParameter(str(exc)) from exc
    event = record_event(
        root,
        "project_created",
        {
            "source": source_rel,
            "target_artifact": target_artifact,
            "bootstrap_run_id": bootstrap_result["run_id"],
            "references": references_result,
            "inherited_boundary": _inherit_event_summary(inherit_result),
            "checkpoint": checkpoint_result,
        },
    )
    return {
        "schema_version": "iteris.new.v0",
        "project_path": str(root),
        "project_id": init_result["project_id"],
        "source": source_rel,
        "problem_id": problem_id,
        "target_artifact": target_artifact,
        "references_dir": "references/",
        "references": references_result,
        "inherited_boundary": inherit_result,
        "bootstrap": bootstrap_result,
        "git": checkpoint_result.get("status", git_init.get("status")),
        "git_init": git_init,
        "checkpoint": checkpoint_result,
        "event_id": event["event_id"],
        "preexisting_files": preexisting_files,
        "preexisting_files_committed": False,
    }


def new(
    project_path: str = typer.Argument(".", help="Project directory. Defaults to the current directory."),
    source: str | None = typer.Option(None, "--source", "-s", help="Primary source/problem file."),
    references: list[str] = typer.Option([], "--references", "-r", help="Reference file or directory to copy into references/user/ and index in references/MANIFEST.json. Repeatable."),
    inherit_frontier: str | None = typer.Option(None, "--inherit-frontier", help="Prior Iteris project on the same problem. Imports its verified blockers, rejected claims, and closed lanes as advisory boundary knowledge."),
    allow_non_empty: bool = typer.Option(False, "--allow-non-empty", help="Initialize inside a non-empty directory that is not already an Iteris project."),
    force: bool = typer.Option(False, "--force", help="Overwrite Iteris-managed files when reinitializing."),
    json_output: bool = typer.Option(False, "--json", help="Print machine-readable JSON."),
) -> None:
    """Create a ready-to-run Iteris project without starting the agent loop."""
    root = Path(project_path).resolve()
    if source is None:
        raise typer.BadParameter("provide --source /path/to/problem.tex")
    source_path = Path(source).resolve()
    reference_paths = [Path(item) for item in references]
    inherit_root = Path(inherit_frontier).resolve() if inherit_frontier else None

    if not json_output:
        log.header("iteris new")
    payload = perform_new_project(
        root,
        source=source_path,
        references=reference_paths,
        inherit_frontier=inherit_root,
        allow_non_empty=allow_non_empty,
        force=force,
    )
    if payload.get("already_exists"):
        if json_output:
            typer.echo(json.dumps(payload, indent=2, ensure_ascii=False))
            return
        log.warn("This directory is already an Iteris project; no files were changed.")
        _print_ready(payload, already_exists=True)
        return
    if json_output:
        typer.echo(json.dumps(payload, indent=2, ensure_ascii=False))
        return
    _print_ready(payload, already_exists=False)


def _existing_project_payload(root: Path) -> dict[str, Any]:
    problem_id, target_artifact = resolve_goal_defaults(root)
    source = source_file(root)
    return {
        "schema_version": "iteris.new.v0",
        "project_path": str(root),
        "project_id": problem_id,
        "source": str(source.relative_to(root)) if source else None,
        "problem_id": problem_id,
        "target_artifact": target_artifact,
        "references_dir": "references/",
        "already_exists": True,
    }


def _preexisting_files(root: Path) -> list[str]:
    if not root.exists():
        return []
    return sorted(str(path.relative_to(root)) for path in root.rglob("*") if path.is_file() and ".git" not in path.parts)


def _inherit_event_summary(inherit_result: dict[str, Any] | None) -> dict[str, Any] | None:
    if not inherit_result:
        return None
    return {
        "parent_project": inherit_result.get("parent_project"),
        "imported_fact_count": len(inherit_result.get("imported_facts") or []),
        "do_not_schedule_patterns": len(inherit_result.get("do_not_schedule_patterns") or []),
        "summary_path": inherit_result.get("summary_path"),
    }


def _initial_checkpoint_paths(root: Path, *, source_rel: str | None, bootstrap_result: dict[str, Any]) -> list[str]:
    paths = [
        ".gitignore",
        "references/MANIFEST.json",
        "references/user",
        "references/processed",
        ".iteris/inherit.json",
        ".iteris/INDEX.md",
        ".iteris/OPERATOR.md",
        "docs/OPERATOR.md",
        ".iteris/monitor",
        ".iteris/config.json",
        "iteris.toml",
        "PROJECT.md",
        "ROADMAP.md",
        "STATUS.md",
        "artifacts/ARTIFACT_INDEX.jsonl",
        "artifacts/README.md",
        "references/README.md",
        "memory/facts/FACT_INDEX.jsonl",
        "memory/facts/FRONTIER_INDEX.json",
        "memory/scratch/branch_states.jsonl",
        "memory/scratch/decisions.jsonl",
        "memory/scratch/events.jsonl",
        "memory/scratch/failed_paths.jsonl",
        "memory/scratch/observations.jsonl",
        "tasks/TASK_BOARD.jsonl",
        "tasks/TASK_POOL.json",
        "verification/VERIFICATION_INDEX.jsonl",
    ]
    if source_rel:
        paths.append(source_rel)
    for key in ["run_dir", "fact_path"]:
        value = bootstrap_result.get(key)
        if value:
            paths.append(_rel(root, Path(str(value))))
    task_id = bootstrap_result.get("task_id")
    if task_id:
        paths.append(f"tasks/{task_id}.json")
    verification_id = bootstrap_result.get("verification_request_id")
    if verification_id:
        paths.extend([f"verification/requests/{verification_id}.json", f"verification/results/{verification_id}.json"])
    return [path for path in paths if (root / path).exists()]


def _rel(root: Path, path: Path) -> str:
    try:
        return str(path.resolve().relative_to(root))
    except ValueError:
        return str(path)


def _write_status(root: Path, *, target_artifact: str) -> None:
    source = source_file(root)
    status = [
        "phase: ready",
        f"last_updated: {now_iso()}",
        f"source: {source.relative_to(root) if source else ''}",
        f"target_artifact: {target_artifact}",
        "next: iteris run",
        "",
    ]
    (root / "STATUS.md").write_text("\n".join(status), encoding="utf-8")


def _print_ready(payload: dict[str, Any], *, already_exists: bool) -> None:
    title = "Existing Iteris project" if already_exists else "Iteris project ready"
    log.panel(
        "\n".join(_ready_lines(payload)),
        title=title,
    )


def _ready_lines(payload: dict[str, Any]) -> list[str]:
    lines = [
        f"Project: {payload['project_path']}",
        f"Source: {payload.get('source') or '(not set)'}",
        f"Target: {payload['target_artifact']}",
        "References: put optional papers, notes, PDFs, or source material in references/",
    ]
    if payload.get("references"):
        lines.append(
            f"Imported references: {payload['references']['total_files']} file(s) indexed in references/MANIFEST.json"
        )
    if payload.get("inherited_boundary"):
        inherited = payload["inherited_boundary"]
        lines.append(
            f"Inherited boundary: {len(inherited.get('imported_facts') or [])} advisory fact(s) from {inherited.get('parent_project')}"
        )
    if payload.get("preexisting_files"):
        lines.extend(["", "Existing files were left uncommitted. Review them before checkpointing."])
    lines.extend(
        [
            "",
            "Start work:",
            "  cd " + payload["project_path"],
            "  iteris run",
            "",
            "Monitor or control the run:",
            "  iteris monitor",
            "  iteris dashboard",
            "  iteris status",
            "  iteris attach",
            "  iteris stop",
            "  iteris review",
        ]
    )
    return lines

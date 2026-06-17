"""Run-log bundling commands."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
from pathlib import Path
from typing import Any

import typer

from iteris import log
from iteris.codex_logs import CODEX_RUN_INDEX
from iteris.commands.goal import latest_goal_logs
from iteris.gitops import status as git_status
from iteris.project import now_iso, now_stamp, read_json, require_project, slugify, write_json

app = typer.Typer(help="Bundle reproducibility logs for Iteris runs.")

ANSI_RE = re.compile(r"\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~]|\][^\x07]*(?:\x07|\x1B\\))")
SESSION_ID_RE = re.compile(r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b", re.IGNORECASE)


def strip_ansi(text: str) -> str:
    return ANSI_RE.sub("", text)


def create_log_bundle(project_root: Path, *, session: str | None = None) -> dict[str, Any]:
    root = project_root.resolve()
    selected = _select_goal_logs(root, session=session)
    selected_session = session or str(selected.get("session_name") or root.name)
    bundle_id = f"run-bundle-{now_stamp()}-{slugify(selected_session, 40)}"
    bundle_dir = root / "artifacts" / "run_bundles" / bundle_id
    bundle_dir.mkdir(parents=True, exist_ok=True)

    copied: list[dict[str, Any]] = []
    references: list[dict[str, Any]] = []
    warnings: list[str] = []

    meta_path = _path_or_none(selected.get("meta"))
    pane_log_path = _path_or_none(selected.get("pane_log"))
    if meta_path:
        copied.append(_copy_with_hash(root, meta_path, bundle_dir / "goal.meta.json", kind="goal_meta"))
    if pane_log_path:
        raw_copy = bundle_dir / "pane.raw.log"
        copied.append(_copy_with_hash(root, pane_log_path, raw_copy, kind="pane_log_raw"))
        transcript = bundle_dir / "pane.transcript.txt"
        transcript.write_text(strip_ansi(pane_log_path.read_text(encoding="utf-8", errors="replace")), encoding="utf-8")
        copied.append(_file_record(root, transcript, kind="pane_log_transcript"))
    elif session:
        warnings.append(f"no pane log found for session {session}")
    if session and not meta_path:
        warnings.append(f"no launch metadata found for session {session}")

    for rel, kind in [
        ("STATUS.md", "status"),
        ("tasks/TASK_POOL.json", "task_pool"),
        ("artifacts/ARTIFACT_INDEX.jsonl", "artifact_index"),
        (".iteris/logs/events.jsonl", "event_log"),
        (CODEX_RUN_INDEX, "codex_run_index"),
    ]:
        source = root / rel
        if source.exists():
            copied.append(_copy_with_hash(root, source, bundle_dir / Path(rel).name, kind=kind))

    copied.extend(_copy_project_codex_logs(root, bundle_dir))

    for pattern, kind in [
        ("verification/results/*.json", "verification_result"),
        ("artifacts/*/*/*/artifact_manifest.json", "artifact_manifest"),
        ("artifacts/agent_runs/*/output.json", "agent_output"),
        ("artifacts/agent_runs/*/status.json", "agent_status"),
    ]:
        for path in sorted(root.glob(pattern), key=lambda item: item.stat().st_mtime, reverse=True)[:25]:
            references.append(_file_record(root, path, kind=kind))

    rollout_records = _copy_codex_rollouts(root, bundle_dir, copied_files=copied)
    if rollout_records:
        rollouts_manifest = {
            "schema_version": "iteris.codex_rollouts_manifest.v0",
            "created_at": now_iso(),
            "rollouts": rollout_records,
        }
        write_json(bundle_dir / "codex_rollouts_manifest.json", rollouts_manifest)
        copied.append(_file_record(root, bundle_dir / "codex_rollouts_manifest.json", kind="codex_rollouts_manifest"))

    manifest = {
        "schema_version": "iteris.log_bundle.v0",
        "bundle_id": bundle_id,
        "created_at": now_iso(),
        "project_path": str(root),
        "session_name": selected_session,
        "source_logs": selected,
        "git": git_status(root),
        "warnings": warnings,
        "copied_files": copied,
        "referenced_files": references,
    }
    write_json(bundle_dir / "manifest.json", manifest)
    manifest["manifest_path"] = str((bundle_dir / "manifest.json").relative_to(root))
    write_json(bundle_dir / "manifest.json", manifest)
    return manifest


def _select_goal_logs(root: Path, *, session: str | None) -> dict[str, str | None]:
    if session:
        logs = latest_goal_logs(root, session)
        return {"session_name": session, **logs}
    logs_dir = root / ".iteris" / "logs"
    metas = sorted(logs_dir.glob("goal-*.meta.json"), key=lambda path: path.stat().st_mtime, reverse=True)
    if not metas:
        return {"session_name": None, "meta": None, "pane_log": None}
    meta = metas[0]
    payload = read_json(meta, default={})
    session_name = payload.get("session_name") if isinstance(payload, dict) else None
    pane_log = payload.get("pane_log") if isinstance(payload, dict) else None
    pane_path = root / str(pane_log) if pane_log else meta.with_suffix(".pane.log")
    return {
        "session_name": str(session_name) if session_name else None,
        "meta": str(meta),
        "pane_log": str(pane_path) if pane_path.exists() else None,
    }


def _copy_with_hash(root: Path, source: Path, dest: Path, *, kind: str) -> dict[str, Any]:
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, dest)
    record = _file_record(root, dest, kind=kind)
    record["source_path"] = _rel(root, source)
    return record


def _copy_project_codex_logs(root: Path, bundle_dir: Path) -> list[dict[str, Any]]:
    copied: list[dict[str, Any]] = []
    patterns = [
        ("artifacts/agent_runs/*/codex.events.jsonl", "codex_exec_events"),
        ("artifacts/agent_runs/*/codex.log", "codex_exec_text_log"),
        ("artifacts/agent_runs/*/codex.stderr.log", "codex_exec_stderr"),
        ("artifacts/agent_runs/*/log_manifest.json", "codex_exec_manifest"),
        ("verification/agent_runs/*/codex.events.jsonl", "codex_exec_events"),
        ("verification/agent_runs/*/codex.log", "codex_exec_text_log"),
        ("verification/agent_runs/*/codex.stderr.log", "codex_exec_stderr"),
        ("verification/agent_runs/*/log_manifest.json", "codex_exec_manifest"),
    ]
    seen: set[Path] = set()
    for pattern, kind in patterns:
        for source in sorted(root.glob(pattern)):
            resolved = source.resolve()
            if resolved in seen:
                continue
            seen.add(resolved)
            copied.append(_copy_with_hash(root, source, bundle_dir / "project_codex_logs" / source.relative_to(root), kind=kind))
    return copied


def _copy_codex_rollouts(root: Path, bundle_dir: Path, *, copied_files: list[dict[str, Any]]) -> list[dict[str, Any]]:
    session_ids = _session_ids_from_paths(root, [root / str(item["path"]) for item in copied_files])
    if not session_ids:
        return []
    sessions_root = Path(os.getenv("CODEX_HOME", str(Path.home() / ".codex"))) / "sessions"
    if not sessions_root.exists():
        return []
    out_dir = bundle_dir / "codex_rollouts"
    records: list[dict[str, Any]] = []
    for session_id in sorted(session_ids):
        for source in sorted(sessions_root.rglob(f"rollout-*{session_id}.jsonl")):
            dest = out_dir / source.name
            if dest.exists():
                dest = out_dir / f"{session_id}-{len(records)}.jsonl"
            record = _copy_with_hash(root, source, dest, kind="codex_rollout_jsonl")
            record["session_id"] = session_id
            records.append(record)
    return records


def _session_ids_from_paths(root: Path, paths: list[Path]) -> set[str]:
    ids: set[str] = set()
    for path in paths:
        resolved = path if path.is_absolute() else root / path
        if not resolved.exists() or resolved.is_dir():
            continue
        text = resolved.read_text(encoding="utf-8", errors="replace")
        ids.update(match.group(0).lower() for match in SESSION_ID_RE.finditer(text))
    return ids


def _file_record(root: Path, path: Path, *, kind: str) -> dict[str, Any]:
    return {
        "kind": kind,
        "path": _rel(root, path),
        "size": path.stat().st_size,
        "sha256": _sha256(path),
    }


def _path_or_none(value: object) -> Path | None:
    if not value:
        return None
    path = Path(str(value))
    return path if path.exists() else None


def _rel(root: Path, path: Path) -> str:
    try:
        return str(path.resolve().relative_to(root))
    except ValueError:
        return str(path.resolve())


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


@app.command("bundle")
def bundle(
    project_path: str = typer.Argument(".", help="Iteris project path."),
    session: str | None = typer.Option(None, "--session", "-s", help="Goal tmux session name. Defaults to latest goal log."),
    json_output: bool = typer.Option(False, "--json", help="Print machine-readable JSON."),
) -> None:
    """Create a project-local bundle for reproducing a goal run."""
    root = require_project(project_path)
    manifest = create_log_bundle(root, session=session)
    if json_output:
        typer.echo(json.dumps(manifest, indent=2, ensure_ascii=False))
        return
    log.success(f"Created log bundle {manifest['bundle_id']}")
    log.key_value(
        {
            "Manifest": str(manifest["manifest_path"]),
            "Session": str(manifest["session_name"]),
            "Copied files": str(len(manifest["copied_files"])),
            "References": str(len(manifest["referenced_files"])),
        }
    )

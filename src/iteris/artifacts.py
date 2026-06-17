"""Artifact layout helpers."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from iteris.project import append_jsonl, now_iso, read_json, slugify, write_json


MODE_ARTIFACT_ROOTS = {
    "foundation": "artifacts/route_checks",
    "proof": "artifacts/proofs",
    "experiment": "artifacts/experiments",
    "algorithm": "artifacts/code",
}
INDEX_PATH = "artifacts/ARTIFACT_INDEX.jsonl"
MANIFEST_NAME = "artifact_manifest.json"
SCRIPT_SUFFIXES = {".py", ".sh", ".bash", ".zsh", ".R", ".jl", ".m", ".sage", ".lean", ".ipynb", ".js", ".ts"}
DOCUMENT_SUFFIXES = {".md"}
REQUIRED_INDEX_SUFFIXES = SCRIPT_SUFFIXES | DOCUMENT_SUFFIXES
CANONICAL_KIND_DIRS = {"route_checks", "proofs", "experiments", "code"}


def artifact_root_for(role: str | None, mode: str | None) -> str:
    if role == "explore":
        return "artifacts/route_checks"
    return MODE_ARTIFACT_ROOTS.get(str(mode or ""), "artifacts/route_checks")


def create_artifact_workspace(
    project_root: Path,
    *,
    run_id: str,
    role: str | None,
    mode: str | None,
    task_id: str | None,
    focus: str | None,
    agent_run_dir: Path,
) -> dict[str, Any]:
    root = project_root.resolve()
    base_rel = artifact_root_for(role, mode)
    label = slugify(task_id or focus or run_id, 72)
    workspace = root / base_rel / label / run_id
    workspace.mkdir(parents=True, exist_ok=True)
    manifest_path = workspace / "artifact_manifest.json"
    request = {
        "artifact_index": INDEX_PATH,
        "artifact_workspace": str(workspace.relative_to(root)),
        "artifact_manifest": str(manifest_path.relative_to(root)),
        "recommended_artifacts": recommended_artifacts(str(workspace.relative_to(root)), role=role, mode=mode),
    }
    manifest = {
        "schema_version": "iteris.artifact_manifest.v0",
        "run_id": run_id,
        "role": role,
        "mode": mode,
        "task_id": task_id,
        "focus": focus,
        "created_at": now_iso(),
        "updated_at": now_iso(),
        "agent_run_dir": str(agent_run_dir.relative_to(root)),
        "artifact_workspace": request["artifact_workspace"],
        "recommended_artifacts": request["recommended_artifacts"],
        "created_artifacts": [],
        "updated_shared_files": [],
        "candidate_facts": [],
        "verification_requests": [],
        "task_pool_updates": [],
    }
    write_json(manifest_path, manifest)
    append_jsonl(
        root / INDEX_PATH,
        {
            "schema_version": "iteris.artifact_index_record.v0",
            "record_type": "workspace_created",
            "created_at": manifest["created_at"],
            "run_id": run_id,
            "role": role,
            "mode": mode,
            "task_id": task_id,
            "focus": focus,
            "artifact_workspace": request["artifact_workspace"],
            "artifact_manifest": request["artifact_manifest"],
            "agent_run_dir": manifest["agent_run_dir"],
        },
    )
    return request


def recommended_artifacts(workspace: str, *, role: str | None, mode: str | None) -> dict[str, str]:
    if role == "explore":
        return {
            "exploration_report": f"{workspace}/exploration_report.md",
            "candidate_routes": f"{workspace}/candidate_routes.json",
        }
    if mode == "proof":
        return {
            "proof_report": f"{workspace}/proof.md",
            "verification_claim": f"{workspace}/verification_claim.md",
        }
    if mode == "experiment":
        return {
            "experiment_report": f"{workspace}/report.md",
            "script": f"{workspace}/experiment.py",
            "config": f"{workspace}/config.json",
            "raw_results": f"{workspace}/results.json",
            "hypotheses": f"{workspace}/hypotheses.jsonl",
            "instances": f"{workspace}/instances.jsonl",
            "best_cases": f"{workspace}/best_cases.json",
            "failure_cases": f"{workspace}/failure_cases.json",
            "feature_report": f"{workspace}/feature_analysis.md",
            "exactification_plan": f"{workspace}/exactification_plan.md",
        }
    if mode == "algorithm":
        return {
            "algorithm_report": f"{workspace}/algorithm.md",
            "prototype": f"{workspace}/prototype.py",
            "implementation_notes": f"{workspace}/implementation_notes.md",
        }
    return {
        "route_report": f"{workspace}/route_report.md",
        "source_notes": f"{workspace}/source_notes.md",
    }


def update_manifest_from_agent_output(project_root: Path, request: dict[str, Any], output: dict[str, Any] | None, *, status: str) -> None:
    manifest_rel = request.get("artifact_manifest")
    if not isinstance(manifest_rel, str) or not manifest_rel:
        return
    manifest_path = project_root / manifest_rel
    manifest = read_json(manifest_path, default={})
    if not isinstance(manifest, dict):
        manifest = {}
    manifest.setdefault("schema_version", "iteris.artifact_manifest.v0")
    manifest["updated_at"] = now_iso()
    manifest["agent_status"] = status
    if output:
        for key in [
            "created_artifacts",
            "updated_shared_files",
            "candidate_facts",
            "verification_requests",
            "task_pool_updates",
            "frontier_updates",
            "next_actions",
        ]:
            if key in output:
                manifest[key] = output[key]
        manifest["agent_output_summary"] = output.get("summary")
        manifest["agent_output_status_recommendation"] = output.get("status_recommendation")
    write_json(manifest_path, manifest)
    append_jsonl(
        project_root / INDEX_PATH,
        {
            "schema_version": "iteris.artifact_index_record.v0",
            "record_type": "agent_output_indexed",
            "created_at": now_iso(),
            "run_id": request.get("run_id"),
            "role": request.get("role"),
            "mode": request.get("mode"),
            "task_id": request.get("task_id"),
            "agent_status": status,
            "artifact_workspace": request.get("artifact_workspace"),
            "artifact_manifest": request.get("artifact_manifest"),
            "created_artifacts": manifest.get("created_artifacts", []),
            "updated_shared_files": manifest.get("updated_shared_files", []),
            "candidate_facts": manifest.get("candidate_facts", []),
            "verification_requests": manifest.get("verification_requests", []),
            "frontier_updates": manifest.get("frontier_updates", []),
        },
    )


def artifact_layout_summary() -> dict[str, str]:
    return {
        "artifacts/agent_runs/": "Raw subagent prompts, logs, status, and structured outputs; append-only debugging trail.",
        "artifacts/route_checks/": "Foundation/explore route summaries and compatibility summaries for older workflows.",
        "artifacts/proofs/": "Proof attempts, lemma chains, verification claims, and proof-grade reports.",
        "artifacts/experiments/": "Reproducible experiment scripts, configs, raw outputs, and interpretation reports.",
        "artifacts/code/": "Reusable prototypes or implementation artifacts produced by algorithm tasks.",
        "artifacts/references/": "Theorem search records, arXiv source fetches, and processed external references.",
        "artifacts/run_bundles/": "Review and reproducibility bundles with log manifests and hashes.",
        "artifacts/ARTIFACT_INDEX.jsonl": "Append-only global index of artifact workspaces and completed agent outputs.",
    }


def read_artifact_index(project_root: Path) -> list[dict[str, Any]]:
    path = project_root / INDEX_PATH
    if not path.exists():
        return []
    records: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if not line.strip():
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            records.append({"schema_version": "iteris.artifact_index_record.invalid", "raw": line})
            continue
        if isinstance(payload, dict):
            records.append(payload)
    return records


def list_artifact_manifests(project_root: Path) -> list[Path]:
    root = project_root.resolve()
    artifacts = root / "artifacts"
    if not artifacts.exists():
        return []
    return sorted(
        path
        for path in artifacts.glob(f"*/*/*/{MANIFEST_NAME}")
        if path.is_file() and _is_canonical_manifest(root, path)
    )


def artifact_gate(project_root: Path) -> dict[str, Any]:
    root = project_root.resolve()
    errors: list[dict[str, str]] = []
    warnings: list[dict[str, str]] = []
    index_records = read_artifact_index(root)
    manifest_paths = list_artifact_manifests(root)
    indexed_paths = _indexed_artifact_paths(root, index_records, manifest_paths, errors, warnings)

    if not (root / INDEX_PATH).exists():
        errors.append({"location": INDEX_PATH, "issue": "global artifact index is missing"})

    for path in _artifact_files(root):
        rel = _rel(path, root)
        if rel == INDEX_PATH or rel.endswith(f"/{MANIFEST_NAME}") or rel.startswith("artifacts/agent_runs/"):
            continue
        canonical = _is_in_canonical_workspace(root, path)
        indexed = rel in indexed_paths
        if path.suffix in REQUIRED_INDEX_SUFFIXES and canonical and not indexed:
            kind = "script" if path.suffix in SCRIPT_SUFFIXES else "document"
            errors.append({"location": rel, "issue": f"{kind} artifact is not listed in its workspace manifest or artifact index"})
        elif path.suffix in SCRIPT_SUFFIXES and not canonical:
            warnings.append({"location": rel, "issue": "script artifact is outside the canonical artifacts/<kind>/<task-label>/<run-id>/ layout"})
        elif canonical and not indexed and path.name not in {"README.md"}:
            warnings.append({"location": rel, "issue": "artifact file is not listed in its workspace manifest or artifact index"})

    return {
        "schema_version": "iteris.artifact_gate.v0",
        "ok": not errors,
        "errors": errors,
        "warnings": warnings,
        "artifact_index": INDEX_PATH,
        "index_record_count": len(index_records),
        "manifest_count": len(manifest_paths),
        "script_suffixes": sorted(SCRIPT_SUFFIXES),
        "required_index_suffixes": sorted(REQUIRED_INDEX_SUFFIXES),
    }


def artifact_index_summary(project_root: Path, *, limit: int = 20) -> dict[str, Any]:
    root = project_root.resolve()
    records = read_artifact_index(root)
    manifests = list_artifact_manifests(root)
    by_mode: dict[str, int] = {}
    for record in records:
        mode = str(record.get("mode") or "unknown")
        by_mode[mode] = by_mode.get(mode, 0) + 1
    return {
        "schema_version": "iteris.artifact_index_summary.v0",
        "artifact_index": INDEX_PATH,
        "index_record_count": len(records),
        "manifest_count": len(manifests),
        "by_mode": by_mode,
        "recent_records": records[-limit:],
        "recent_manifests": [_rel(path, root) for path in manifests[-limit:]],
    }


def search_artifacts(project_root: Path, query: str, *, limit: int = 10) -> list[dict[str, Any]]:
    root = project_root.resolve()
    terms = [term.lower() for term in query.split() if term.strip()]
    if not terms:
        return []
    candidates: list[tuple[int, dict[str, Any]]] = []
    for record in read_artifact_index(root):
        text = json.dumps(record, ensure_ascii=False).lower()
        score = sum(text.count(term) for term in terms)
        if score:
            candidates.append((score, {"kind": "index_record", "score": score, "record": record}))
    for path in list_artifact_manifests(root):
        payload = read_json(path, default={})
        text = json.dumps(payload, ensure_ascii=False).lower()
        score = sum(text.count(term) for term in terms)
        if score:
            candidates.append(
                (
                    score,
                    {
                        "kind": "manifest",
                        "score": score,
                        "path": _rel(path, root),
                        "summary": payload.get("agent_output_summary") if isinstance(payload, dict) else None,
                        "task_id": payload.get("task_id") if isinstance(payload, dict) else None,
                        "mode": payload.get("mode") if isinstance(payload, dict) else None,
                        "created_artifacts": payload.get("created_artifacts", []) if isinstance(payload, dict) else [],
                    },
                )
            )
    candidates.sort(key=lambda item: item[0], reverse=True)
    return [item for _, item in candidates[:limit]]


def _indexed_artifact_paths(
    root: Path,
    index_records: list[dict[str, Any]],
    manifest_paths: list[Path],
    errors: list[dict[str, str]],
    warnings: list[dict[str, str]],
) -> set[str]:
    indexed: set[str] = set()
    required_manifest_fields = {"schema_version", "run_id", "role", "artifact_workspace", "agent_run_dir"}
    manifest_rels = {_rel(path, root) for path in manifest_paths}
    index_manifest_rels = {str(record.get("artifact_manifest")) for record in index_records if record.get("artifact_manifest")}
    for rel in manifest_rels - index_manifest_rels:
        warnings.append({"location": rel, "issue": "workspace manifest is not referenced by the global artifact index"})
    for path in manifest_paths:
        rel = _rel(path, root)
        payload = read_json(path, default={})
        if not isinstance(payload, dict):
            errors.append({"location": rel, "issue": "artifact manifest must be a JSON object"})
            continue
        missing = sorted(field for field in required_manifest_fields if not payload.get(field))
        if missing:
            errors.append({"location": rel, "issue": f"artifact manifest missing required fields: {', '.join(missing)}"})
        if payload.get("schema_version") != "iteris.artifact_manifest.v0":
            errors.append({"location": rel, "issue": "artifact manifest has an invalid schema_version"})
        for item in payload.get("created_artifacts") or []:
            item_rel = str(item)
            indexed.add(item_rel)
            if not (root / item_rel).exists():
                errors.append({"location": item_rel, "issue": f"manifest references missing artifact from {rel}"})
    for record in index_records:
        for item in record.get("created_artifacts") or []:
            item_rel = str(item)
            indexed.add(item_rel)
            if not (root / item_rel).exists():
                errors.append({"location": item_rel, "issue": "artifact index references missing artifact"})
    return indexed


def _artifact_files(root: Path) -> list[Path]:
    artifacts = root / "artifacts"
    if not artifacts.exists():
        return []
    return sorted(path for path in artifacts.rglob("*") if path.is_file())


def _is_canonical_manifest(root: Path, path: Path) -> bool:
    return path.name == MANIFEST_NAME and _is_in_canonical_workspace(root, path)


def _is_in_canonical_workspace(root: Path, path: Path) -> bool:
    try:
        rel = path.resolve().relative_to(root / "artifacts")
    except ValueError:
        return False
    parts = rel.parts
    return len(parts) >= 4 and parts[0] in CANONICAL_KIND_DIRS


def _rel(path: Path, root: Path) -> str:
    return str(path.resolve().relative_to(root))

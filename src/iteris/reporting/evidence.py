"""Evidence collection for versioned Iteris reports."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from iteris.project import now_iso
from iteris.reporting.utils import (
    guess_target_artifact,
    natural_proof_path,
    ordered_unique,
    parse_status,
    relative_project_path,
)
from iteris.verification.local import latest_results

EVIDENCE_SCHEMA = "iteris.report_evidence.v0"


def collect_evidence(project_root: Path, *, report_id: str) -> dict[str, Any]:
    root = project_root.resolve()
    status = parse_status(root / "STATUS.md")
    target_artifact = relative_project_path(root, str(status.get("target_artifact") or guess_target_artifact(root) or ""))
    verification_results = latest_results(root)
    goal = _find_verification(
        verification_results,
        request_id=str(status.get("terminal_goal_success_verification") or ""),
        mode="goal_success",
    )
    assembly = _find_verification(
        verification_results,
        request_id=str(status.get("terminal_assembly_verification") or ""),
        mode="assembly",
    )
    checked_fact_ids = ordered_unique(
        [str(item) for item in status.get("verified_facts", []) if item]
        + [str(item) for item in (goal or {}).get("checked_fact_ids", []) if item]
        + [str(item) for item in (assembly or {}).get("checked_fact_ids", []) if item]
    )
    fact_rows = _fact_rows(root)
    fact_by_id = {str(row.get("fact_id")): row for row in fact_rows if row.get("fact_id")}
    facts = [_compact_fact_row(fact_by_id[fid]) for fid in checked_fact_ids if fid in fact_by_id]

    source_paths = _ordered_path_records(
        root,
        [
            ("status", "STATUS.md"),
            ("fact_index", "memory/facts/FACT_INDEX.jsonl"),
            ("target_artifact", target_artifact),
            ("natural_proof", natural_proof_path(root, target_artifact)),
            ("goal_success_verification", _verification_result_path(goal)),
            ("assembly_verification", _verification_result_path(assembly)),
        ],
    )
    return {
        "schema_version": EVIDENCE_SCHEMA,
        "report_id": report_id,
        "generated_at": now_iso(),
        "evidence_mode_default": "linked",
        "answer": {
            "target_artifact": target_artifact,
            "target_exists": bool(target_artifact and (root / target_artifact).exists()),
            "answer_type": status.get("answer_type"),
            "verified_positive_result": status.get("verified_positive_result"),
            "assembly_verification": (assembly or {}).get("request_id") or status.get("terminal_assembly_verification"),
            "goal_success_verification": (goal or {}).get("request_id") or status.get("terminal_goal_success_verification"),
            "goal_success_summary": (goal or {}).get("summary", ""),
        },
        "fact_graph": {
            "fact_index": "memory/facts/FACT_INDEX.jsonl",
            "fact_count": len(fact_rows),
            "checked_fact_ids": checked_fact_ids,
        },
        "facts": facts,
        "source_paths": source_paths,
        "checked_artifacts": _checked_artifacts(root, goal, assembly),
        "sections": [
            {
                "section_id": "main_result",
                "uses": {"fact_ids": checked_fact_ids, "paths": [target_artifact] if target_artifact else []},
            },
            {
                "section_id": "evidence_appendix",
                "uses": {
                    "fact_ids": checked_fact_ids,
                    "paths": [item["path"] for item in source_paths if item.get("path")],
                },
            },
        ],
    }


def _fact_rows(root: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    path = root / "memory" / "facts" / "FACT_INDEX.jsonl"
    if not path.exists():
        return rows
    for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if not raw.strip():
            continue
        try:
            row = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if isinstance(row, dict):
            rows.append(row)
    return rows


def _compact_fact_row(row: dict[str, Any]) -> dict[str, Any]:
    keys = [
        "fact_id",
        "origin_fact_id",
        "status",
        "fact_type",
        "review_level",
        "claim_policy",
        "claim_summary",
        "path",
        "predecessors",
        "verification",
    ]
    return {key: row[key] for key in keys if key in row}


def _checked_artifacts(root: Path, *results: dict[str, Any] | None) -> list[dict[str, str]]:
    records: list[dict[str, str]] = []
    seen: set[str] = set()
    for result in results:
        if not result:
            continue
        for item in result.get("checked_artifacts") or []:
            text = str(item)
            if text in seen:
                continue
            seen.add(text)
            if text.startswith(("http://", "https://")):
                records.append({"kind": "url", "path": text})
            else:
                rel = relative_project_path(root, text)
                if rel:
                    records.append({"kind": "project_path", "path": rel})
    return records


def _ordered_path_records(root: Path, pairs: list[tuple[str, str | None]]) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    seen: set[str] = set()
    for role, value in pairs:
        rel = relative_project_path(root, value or "")
        if not rel or rel in seen:
            continue
        seen.add(rel)
        records.append({"role": role, "path": rel, "exists": (root / rel).exists()})
    return records


def _verification_result_path(result: dict[str, Any] | None) -> str:
    if not result or not result.get("request_id"):
        return ""
    return f"verification/results/{result['request_id']}.json"


def _find_verification(
    results: list[dict[str, Any]],
    *,
    request_id: str = "",
    mode: str,
) -> dict[str, Any] | None:
    if request_id:
        for row in results:
            if row.get("request_id") == request_id:
                return row if row.get("passed") else None
    matching = [row for row in results if row.get("mode") == mode and row.get("passed")]
    if not matching:
        return None
    matching.sort(key=lambda row: str(row.get("created_at") or row.get("request_id") or ""))
    return matching[-1]

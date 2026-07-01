"""Family closure pool export, search merge, and prompt injection tests."""

from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from iteris.cli import app
from iteris.commands.goal.prompt import build_project_context_lines
from iteris.family_pool import export_verified_fact, normalize_pool_row
from iteris.family_scaffold import perform_family_init
from iteris.memory.family import family_search_rows, resolve_family_root
from iteris.memory.search import search_memory
from iteris.project import append_jsonl, init_project, write_json

runner = CliRunner()


def _init_sibling(tmp_path: Path, name: str) -> Path:
    root = tmp_path / name
    init_project(root)
    return root


def _verified_fact(project: Path, fact_id: str, summary: str) -> None:
    fact_path = project / "memory" / "facts" / "fact-test.md"
    fact_path.parent.mkdir(parents=True, exist_ok=True)
    fact_path.write_text(f"# {summary}\n", encoding="utf-8")
    append_jsonl(
        project / "memory" / "facts" / "FACT_INDEX.jsonl",
        {
            "fact_id": fact_id,
            "status": "verified",
            "claim_summary": summary,
            "artifact": "memory/facts/fact-test.md",
            "verification": "verify-fact-1",
        },
    )


def test_export_promotes_fact_to_pool(tmp_path):
    family_root = tmp_path / "family"
    family_root.mkdir()
    sibling = _init_sibling(tmp_path, "child-a")
    import os

    os.symlink(sibling, family_root / "child-a", target_is_directory=True)
    perform_family_init(
        family_root,
        goal="test",
        siblings=[{"sibling_id": "2.6", "path": "child-a"}],
        adopt_symlinks=True,
    )
    fact_id = "fact:child-a:bridge-lemma"
    _verified_fact(sibling, fact_id, "Bridge lemma for FC-7A.")

    entry = export_verified_fact(
        family_root,
        source_project=sibling,
        fact_id=fact_id,
        source_sibling_id="2.6",
        usable_by=["2.7"],
    )
    assert entry["schema_version"] == "iteris.family_pool_entry.v1"
    assert entry["origin_fact_id"] == fact_id

    pool_file = family_root / "memory" / "family" / "FAMILY_INDEX.jsonl"
    assert pool_file.exists()
    rows = [json.loads(line) for line in pool_file.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert rows[0]["source_sibling_id"] == "2.6"


def test_family_search_merges_pool_rows(tmp_path):
    family_root = tmp_path / "family"
    family_root.mkdir()
    (family_root / "memory" / "family").mkdir(parents=True)
    child = _init_sibling(tmp_path, "child-b")
    write_json(
        child / ".iteris" / "family.json",
        {
            "schema_version": "iteris.family_member.v0",
            "family_root": str(family_root),
            "family_id": "test-family",
            "sibling_id": "2.7",
        },
    )
    write_json(family_root / ".iteris" / "FAMILY.json", {"schema_version": "iteris.family_state.v1", "goal": "x", "siblings": []})
    append_jsonl(
        family_root / "memory" / "family" / "FAMILY_INDEX.jsonl",
        {
            "schema_version": "iteris.family_pool_entry.v1",
            "origin_fact_id": "fact:child-a:bridge",
            "source_sibling_id": "2.6",
            "claim_summary": "Shared bridge for product lower bound.",
            "usable_by": ["2.7"],
        },
    )
    assert resolve_family_root(child) == family_root.resolve()
    rows = family_search_rows(child)
    assert any(row.get("origin_fact_id") == "fact:child-a:bridge" for row in rows)
    assert all(row.get("scope") == "family" for row in rows)
    assert all("re-verify locally" in row.get("hint", "") for row in rows if "origin_fact_id" in row)

    hits = search_memory(child, "product lower bridge")
    assert any(h.get("scope") == "family" for h in hits)


def test_goal_prompt_includes_pool_when_marker_present(tmp_path):
    family_root = tmp_path / "family"
    family_root.mkdir()
    (family_root / "memory" / "family").mkdir(parents=True)
    child = _init_sibling(tmp_path, "child-b")
    write_json(
        child / ".iteris" / "family.json",
        {
            "schema_version": "iteris.family_member.v0",
            "family_root": str(family_root),
            "family_id": "test-family",
            "sibling_id": "2.7",
        },
    )
    append_jsonl(
        family_root / "memory" / "family" / "FAMILY_INDEX.jsonl",
        {
            "schema_version": "iteris.family_pool_entry.v1",
            "origin_fact_id": "fact:child-a:bridge",
            "source_sibling_id": "2.6",
            "claim_summary": "Shared bridge for product lower bound.",
            "usable_by": ["2.7"],
        },
    )
    lines = "".join(build_project_context_lines(child))
    assert "family closure group" in lines
    assert "fact:child-a:bridge" in lines
    assert "re-verify locally" in lines


def test_normalize_pool_row_unifies_schemas():
    closure = normalize_pool_row(
        {
            "schema_version": "iteris.family_pool_entry.v1",
            "origin_fact_id": "fact:a:b",
            "claim_summary": "A",
            "source_sibling_id": "2.6",
        }
    )
    evolve = normalize_pool_row(
        {
            "schema_version": "iteris.family_index_line.v0",
            "origin_fact_id": "fact:a:b",
            "curated_summary": "B",
        }
    )
    assert closure["claim_summary"] == "A"
    assert evolve["claim_summary"] == "B"


def test_cli_registers_monitor_doctor_report_family():
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0, result.output
    for name in ("monitor", "doctor", "report", "family"):
        assert name in result.output

    result = runner.invoke(app, ["family", "--help"])
    assert result.exit_code == 0, result.output
    for name in ("new", "init", "export", "pool", "status", "schedule", "start", "run", "stop"):
        assert name in result.output

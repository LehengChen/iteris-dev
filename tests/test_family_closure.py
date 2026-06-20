"""Tests for family scaffold, pool export, and closure context."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from iteris.commands.goal.prompt import build_project_context_lines
from iteris.family import has_family_state, read_family_marker
from iteris.memory.family import resolve_family_root
from iteris.family_pool import export_verified_fact, load_pool
from iteris.family_scaffold import create_family, manifest_to_create_args
from iteris.memory.search import search_memory
from iteris.memory.facts import write_fact, rebuild_fact_index
from iteris.project import init_project, write_json


@pytest.fixture()
def manifest(tmp_path):
    src = tmp_path / "sources"
    src.mkdir()
    (src / "p1.md").write_text("# Problem 1\n\nFind X.\n", encoding="utf-8")
    (src / "p2.md").write_text("# Problem 2\n\nFind Y.\n", encoding="utf-8")
    manifest = {
        "goal": "Close both toy problems.",
        "schedule": {"max_concurrent": 2},
        "siblings": [
            {
                "sibling_id": "A",
                "path": "child-a",
                "source": str(src / "p1.md"),
                "north_star": "Prove X.",
                "target_artifact": "results/a/answer.md",
            },
            {
                "sibling_id": "B",
                "path": "child-b",
                "source": str(src / "p2.md"),
                "north_star": "Prove Y.",
                "target_artifact": "results/b/answer.md",
            },
        ],
    }
    family_root = tmp_path / "toy-family"
    args = manifest_to_create_args(manifest)
    create_family(family_root, **args)
    return family_root


def test_family_new_creates_siblings_and_pool(manifest):
    family_root = manifest
    assert has_family_state(family_root)
    child_a = family_root / "child-a"
    child_b = family_root / "child-b"
    assert (child_a / "iteris.toml").exists()
    assert (child_a / ".iteris" / "watchdog_goal.txt").exists()
    assert (family_root / "memory" / "family").is_dir()
    marker = read_family_marker(child_a)
    assert marker["sibling_id"] == "A"
    assert resolve_family_root(child_a) == family_root.resolve()


def test_export_and_search_pool(manifest):
    family_root = manifest
    child_a = family_root / "child-a"
    write_fact(
        child_a,
        fact_id="fact:child-a:lemma:20260101T000000Z",
        source_task="task-0",
        claim_summary="Toy lemma A holds.",
        statement="A holds.",
        status="verified",
        verification="verify-0",
    )
    rebuild_fact_index(child_a)
    export_verified_fact(
        family_root,
        child_a,
        fact_id="fact:child-a:lemma:20260101T000000Z",
        sibling_id="A",
        usable_by=["B"],
    )
    assert len(load_pool(family_root)) >= 1
    child_b = family_root / "child-b"
    hits = search_memory(child_b, "toy lemma A")
    assert any(h.get("scope") == "family" for h in hits)


def test_goal_prompt_includes_family_block(manifest):
    child_b = manifest / "child-b"
    lines = build_project_context_lines(child_b)
    text = "".join(lines)
    assert "Family closure context" in text
    assert "sibling `B`" in text

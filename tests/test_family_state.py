"""Family closure state and scheduling tests."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from iteris.family import (
    read_family_state,
    schedule_actions,
    sibling_phase,
    write_family_state,
)
from iteris.family_scaffold import perform_family_init
from iteris.project import init_project, write_json


def _write_verified_goal_success(project: Path, *, claim: str, target: str = "results/answer_verified.md") -> None:
    target_path = project / target
    target_path.parent.mkdir(parents=True, exist_ok=True)
    target_path.write_text("# verified\n", encoding="utf-8")
    write_json(
        project / "verification" / "results" / "verify-goal.json",
        {
            "request_id": "verify-goal",
            "mode": "goal_success",
            "passed": True,
            "claim": claim,
            "target_artifact": target,
        },
    )


def _init_sibling(tmp_path: Path, name: str) -> Path:
    root = tmp_path / name
    init_project(root)
    return root


def test_init_adopts_symlink_siblings(tmp_path):
    family_root = tmp_path / "my-family"
    family_root.mkdir()
    sibling_a = _init_sibling(tmp_path, "child-a")
    sibling_b = _init_sibling(tmp_path, "child-b")
    os.symlink(sibling_a, family_root / "child-a")
    os.symlink(sibling_b, family_root / "child-b")

    payload = perform_family_init(
        family_root,
        goal="Close sibling set.",
        siblings=[
            {"sibling_id": "a", "path": "child-a", "target_artifact": "results/a/answer_verified.md"},
            {"sibling_id": "b", "path": "child-b", "target_artifact": "results/b/answer_verified.md"},
        ],
        adopt_symlinks=True,
    )
    assert payload["siblings"] == 2
    state = read_family_state(family_root)
    assert len(state["siblings"]) == 2
    assert (sibling_a / ".iteris" / "family.json").exists()


def test_schedule_respects_max_concurrent(tmp_path, monkeypatch):
    family_root = tmp_path / "family"
    family_root.mkdir()
    siblings = []
    for label in ("a", "b", "c"):
        project = _init_sibling(tmp_path, f"child-{label}")
        os.symlink(project, family_root / f"child-{label}", target_is_directory=True)
        siblings.append(
            {
                "sibling_id": label,
                "path": f"child-{label}",
                "session": f"iteris-{label}",
                "target_artifact": f"results/{label}/answer_verified.md",
            }
        )
    perform_family_init(family_root, goal="test", siblings=siblings, max_concurrent=2, adopt_symlinks=True)

    live = {"iteris-a"}

    def fake_live(session: str) -> bool:
        return session in live

    started: list[str] = []

    def fake_start(family_root, sibling, state, *, dry_run=False):
        started.append(str(sibling["sibling_id"]))
        return {"sibling_id": sibling["sibling_id"], "dry_run": dry_run, "ok": True}

    monkeypatch.setattr("iteris.family.session_live", fake_live)
    monkeypatch.setattr("iteris.family.start_sibling_run", fake_start)

    actions = schedule_actions(family_root, dry_run=True)
    assert len(actions) == 1
    assert started == ["b"]


def test_sibling_phase_closed_requires_claim_prefix(tmp_path):
    family_root = tmp_path / "family"
    family_root.mkdir()
    project = _init_sibling(tmp_path, "child")
    os.symlink(project, family_root / "child", target_is_directory=True)
    perform_family_init(
        family_root,
        goal="test",
        siblings=[{"sibling_id": "2.6", "path": "child", "claim_prefix": "North-Star full closure of Problem 2.6:"}],
        adopt_symlinks=True,
    )
    state = read_family_state(family_root)
    sibling = state["siblings"][0]

    _write_verified_goal_success(project, claim="Wrong prefix")
    assert sibling_phase(family_root, sibling, state) == "open"

    _write_verified_goal_success(project, claim="North-Star full closure of Problem 2.6: done")
    assert sibling_phase(family_root, sibling, state) == "closed"

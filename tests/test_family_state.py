"""Family closure state and scheduling."""

from __future__ import annotations

from pathlib import Path

import pytest

from iteris.family import (
    FamilyError,
    family_status,
    has_family_state,
    init_state,
    read_family_marker,
    resolve_sibling_path,
    schedule_actions,
)
from iteris.project import init_project, write_json


@pytest.fixture()
def family_layout(tmp_path):
    family_root = tmp_path / "my-family"
    family_root.mkdir()
    s1 = tmp_path / "child-a"
    s2 = tmp_path / "child-b"
    init_project(s1)
    init_project(s2)
    (family_root / "child-a").symlink_to(s1)
    (family_root / "child-b").symlink_to(s2)
    (s1 / ".iteris" / "watchdog_goal.txt").write_text("North-Star closure A\n", encoding="utf-8")
    return family_root, s1, s2


def test_init_adopts_child_directories(tmp_path):
    family_root = tmp_path / "my-family"
    family_root.mkdir()
    s1 = family_root / "child-a"
    init_project(s1)
    state = init_state(family_root, goal="Close both tracks.", adopt_symlinks=True)
    assert len(state["siblings"]) == 1


def test_init_adopts_symlink_siblings(family_layout):
    family_root, s1, s2 = family_layout
    state = init_state(family_root, goal="Close both tracks.", adopt_symlinks=True)
    assert has_family_state(family_root)
    assert len(state["siblings"]) == 2
    assert read_family_marker(s1)["sibling_id"] == "child-a"
    assert resolve_sibling_path(family_root, state["siblings"][0]) == s1.resolve()


def test_schedule_respects_max_concurrent(family_layout):
    family_root, _, _ = family_layout
    init_state(family_root, goal="test", schedule={"max_concurrent": 1})
    actions = schedule_actions(family_root)
    assert len(actions) == 1
    assert actions[0]["goal"] == "North-Star closure A"


def test_family_status_counts(family_layout):
    family_root, _, _ = family_layout
    init_state(family_root, goal="test")
    payload = family_status(family_root)
    assert payload["summary"]["total"] == 2
    assert payload["summary"]["open"] == 2
    assert payload["summary"]["closed"] == 0


def test_init_requires_siblings(tmp_path):
    empty = tmp_path / "empty-family"
    empty.mkdir()
    with pytest.raises(FamilyError):
        init_state(empty, goal="x", adopt_symlinks=False, siblings=[])


def test_resolve_sibling_with_evolve_dir(tmp_path):
    family_root = tmp_path / "fam"
    family_root.mkdir()
    evolve = tmp_path / "Iteris-GECP-evo-test"
    init_project(evolve)
    sibling = {"sibling_id": "5.1", "path": "missing-link", "evolve_dir": "Iteris-GECP-evo-test"}
    got = resolve_sibling_path(family_root, sibling)
    assert got.resolve() == evolve.resolve()

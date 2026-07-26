"""Sibling ids must be present and addressable after scaffolding.

A manifest written to match the documented ``--sibling id=...`` CLI spec used
to scaffold successfully while storing ``sibling_id: None``. Everything looked
fine until an id-addressed command ran, and then it failed far from the cause:
``family start --sibling 2.15`` reported ``unknown sibling id: 2.15`` for a
sibling that plainly appeared in ``family status``.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from iteris.family import sibling_by_id
from iteris.family_scaffold import (
    normalize_sibling_entries,
    normalize_sibling_entry,
    perform_family_new,
)


def test_id_is_accepted_as_alias_for_sibling_id():
    entry = normalize_sibling_entry({"id": "2.15", "path": "2.15"}, index=0)
    assert entry["sibling_id"] == "2.15"
    # The alias is consumed so only one spelling reaches the state file.
    assert "id" not in entry


def test_explicit_sibling_id_wins_over_alias():
    entry = normalize_sibling_entry({"sibling_id": "a", "id": "b", "path": "p"}, index=0)
    assert entry["sibling_id"] == "a"


def test_sibling_id_is_coerced_to_string():
    """A bare numeric id in JSON must not break string-keyed lookup."""
    entry = normalize_sibling_entry({"id": 215, "path": "p"}, index=0)
    assert entry["sibling_id"] == "215"


def test_missing_sibling_id_fails_loudly():
    with pytest.raises(ValueError, match="missing sibling_id"):
        normalize_sibling_entry({"path": "2.15"}, index=0)


def test_missing_path_fails_loudly():
    with pytest.raises(ValueError, match="missing path"):
        normalize_sibling_entry({"id": "2.15"}, index=0)


def test_duplicate_sibling_ids_rejected():
    """Duplicates would leave the second sibling unreachable via sibling_by_id."""
    with pytest.raises(ValueError, match="duplicate sibling_id"):
        normalize_sibling_entries(
            [{"id": "2.15", "path": "a"}, {"id": "2.15", "path": "b"}]
        )


def _manifest(tmp_path, key: str) -> tuple[str, list[str]]:
    source = tmp_path / "src.tex"
    source.write_text("\\section{Problem}\nContent.\n", encoding="utf-8")
    ids = ["2.15", "2.16"]
    manifest = {
        "goal": "Close the 2.15-2.16 family",
        "siblings": [{key: sid, "path": sid, "source": str(source)} for sid in ids],
    }
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    return str(manifest_path), ids


@pytest.mark.parametrize("key", ["id", "sibling_id"])
def test_family_new_manifest_siblings_are_addressable(tmp_path, key):
    """Both manifest spellings must yield siblings resolvable by id."""
    family_root = tmp_path / "fam"
    family_root.mkdir()
    manifest_path, ids = _manifest(tmp_path, key)

    result = perform_family_new(family_root, manifest_path=Path(manifest_path))
    # The regression showed up here first: sibling_id came back None.
    assert [row["sibling_id"] for row in result["siblings_created"]] == ids

    state = json.loads((family_root / ".iteris" / "FAMILY.json").read_text(encoding="utf-8"))
    stored = [row["sibling_id"] for row in state["siblings"]]
    assert stored == ids
    # The failure this guards against: scaffolding succeeded but lookup did not.
    for sid in ids:
        assert sibling_by_id(state, sid)["path"] == sid
    # Dotted ids still yield addressable tmux session names.
    assert [row["session"] for row in state["siblings"]] == ["iteris-2_15", "iteris-2_16"]

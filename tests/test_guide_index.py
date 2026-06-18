"""Tests for project INDEX generation."""

from __future__ import annotations

from pathlib import Path

from iteris.guide.index import (
    ROLE_FAMILY_ROOT,
    ROLE_SINGLE,
    build_project_index,
    detect_project_role,
    ensure_project_guide_files,
    parse_project_index,
    read_project_index,
    render_project_index,
)
from iteris.project import init_project


def test_detect_role_single(tmp_path):
    init_project(tmp_path / "proj", source=_write_source(tmp_path))
    assert detect_project_role(tmp_path / "proj") == ROLE_SINGLE


def test_index_created_on_init(tmp_path):
    root = tmp_path / "proj"
    init_project(root, source=_write_source(tmp_path))
    index_path = root / ".iteris" / "INDEX.md"
    assert index_path.exists()
    data = read_project_index(root)
    assert data["role"] == ROLE_SINGLE
    assert data["project_id"]
    assert (root / "docs" / "OPERATOR.md").exists()
    assert (root / ".iteris" / "OPERATOR.md").exists()


def test_parse_render_roundtrip():
    payload = {"schema_version": "iteris.project_index.v0", "title": "demo", "role": "single"}
    text = render_project_index(payload, notes="hello")
    parsed = parse_project_index(text)
    assert parsed["title"] == "demo"
    assert "hello" in text


def test_ensure_project_guide_files_idempotent(tmp_path):
    root = tmp_path / "proj"
    init_project(root, source=_write_source(tmp_path))
    ensure_project_guide_files(root)
    mtime = (root / ".iteris" / "INDEX.md").stat().st_mtime
    ensure_project_guide_files(root)
    assert (root / ".iteris" / "INDEX.md").stat().st_mtime >= mtime


def test_build_project_index_fields(tmp_path):
    root = tmp_path / "proj"
    init_project(root, source=_write_source(tmp_path))
    payload = build_project_index(root)
    assert payload["commands"]["monitor"] == "iteris monitor"
    assert payload["commands"]["report_status"] == "iteris report status"
    assert payload["pointers"]["status"] == "STATUS.md"
    assert payload["pointers"]["reports"] == "reports"
    assert payload["pointers"]["report_index"] == "reports/REPORT_INDEX.jsonl"


def test_evolve_root_role(tmp_path, monkeypatch):
    root = tmp_path / "root"
    init_project(root, source=_write_source(tmp_path))
    (root / "generalize").mkdir(parents=True, exist_ok=True)
    (root / "generalize" / "EVOLVE.json").write_text('{"schema_version":"iteris.evolve_state.v0","goal":"g"}', encoding="utf-8")
    assert detect_project_role(root) == ROLE_FAMILY_ROOT
    payload = build_project_index(root)
    assert payload["evolve"]["initialized"] is True


def _write_source(tmp_path: Path) -> Path:
    src = tmp_path / "problem.tex"
    src.write_text("\\documentclass{article}\\begin{document}hi\\end{document}", encoding="utf-8")
    return src

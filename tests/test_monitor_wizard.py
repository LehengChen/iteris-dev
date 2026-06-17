"""Tests for monitor locale and wizard."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from iteris.guide.locale import choose_locale, normalize_locale, t
from iteris.guide.wizard import _parse_arxiv_ids, run_new_project_wizard


def test_normalize_locale():
    assert normalize_locale("zh-cn") == "zh"
    assert normalize_locale("English") == "en"
    assert normalize_locale("fr") is None


def test_choose_locale_explicit():
    assert choose_locale("en", skip_prompt=True) == "en"


def test_thinking_string():
    assert "思考" in t("zh", "thinking")
    assert "Thinking" in t("en", "thinking")


def test_wizard_arxiv_input_accepts_ids_and_urls():
    parsed = _parse_arxiv_ids(
        "2401.01234, arXiv:2401.01234v2; "
        "https://arxiv.org/abs/math/0309136 "
        "https://arxiv.org/pdf/2501.00001.pdf?download=1"
    )

    assert parsed == ["2401.01234", "2401.01234v2", "math/0309136", "2501.00001"]


def test_exit_words_stay_structured():
    assert "exit" in t("zh", "exit_words")
    assert "quit" in t("en", "exit_words")


def test_wizard_creates_project(tmp_path, monkeypatch):
    src = tmp_path / "problem.tex"
    src.write_text("\\problem{test}", encoding="utf-8")
    project_root = tmp_path / "demo-proj"
    inputs = iter(
        [
            str(project_root),
            str(src),
            "",
            "",
            "optional notes",
            "n",
            "y",
        ]
    )
    monkeypatch.setattr("builtins.input", lambda _prompt="": next(inputs))
    with patch("iteris.guide.wizard._launch_project_run") as mock_run:
        payload = run_new_project_wizard(cwd=tmp_path, locale="zh", executor="codex")
    assert payload is not None
    assert Path(payload["project_path"]).exists()
    assert (project_root / "iteris.toml").exists()
    mock_run.assert_not_called()


def test_wizard_accepts_long_pasted_problem_text(tmp_path, monkeypatch):
    project_root = tmp_path / "demo-proj"
    long_text = "Problem 2.11. " + ("x" * 500)
    inputs = iter([str(project_root), long_text, "", "", "", "n", "y"])
    monkeypatch.setattr("builtins.input", lambda _prompt="": next(inputs))
    with patch("iteris.guide.wizard._launch_project_run"):
        payload = run_new_project_wizard(cwd=tmp_path, locale="zh", executor="codex")
    assert payload is not None
    source = project_root / "sources" / "problem.tex"
    assert source.exists() or (project_root / "problem.tex").exists() or payload.get("source")


def test_wizard_accepts_short_chinese_problem_text(tmp_path, monkeypatch):
    project_root = tmp_path / "demo-proj"
    inputs = iter([str(project_root), "求解黎曼猜想", "", "", "", "n", "y"])
    monkeypatch.setattr("builtins.input", lambda _prompt="": next(inputs))
    with patch("iteris.guide.wizard._launch_project_run"):
        payload = run_new_project_wizard(cwd=tmp_path, locale="zh", executor="codex")
    assert payload is not None
    copied_source = project_root / "sources" / "problem.tex"
    assert copied_source.exists()
    assert "求解黎曼猜想" in copied_source.read_text(encoding="utf-8")


def test_wizard_cancelled_before_create_does_not_create_project(tmp_path, monkeypatch):
    src = tmp_path / "problem.tex"
    src.write_text("\\problem{test}", encoding="utf-8")
    project_root = tmp_path / "demo-proj"
    inputs = iter([str(project_root), str(src), "", "", "", "n", "n"])
    monkeypatch.setattr("builtins.input", lambda _prompt="": next(inputs))
    with patch("iteris.guide.wizard._launch_project_run") as mock_run:
        payload = run_new_project_wizard(cwd=tmp_path, locale="zh", executor="codex")
    assert payload is None
    assert not project_root.exists()
    mock_run.assert_not_called()


def test_wizard_rejects_missing_bare_source_path(tmp_path, monkeypatch):
    project_root = tmp_path / "demo-proj"
    inputs = iter([str(project_root), "missing_source"])
    monkeypatch.setattr("builtins.input", lambda _prompt="": next(inputs))
    with patch("iteris.guide.wizard._launch_project_run"):
        payload = run_new_project_wizard(cwd=tmp_path, locale="zh", executor="codex")
    assert payload is None
    assert not project_root.exists()

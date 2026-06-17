"""Tests for monitor welcome screens."""

from __future__ import annotations

from iteris.guide.welcome import menu_for_role, resolve_menu_input, welcome_text
from iteris.project import init_project


def test_welcome_outside_project_mentions_agent_help():
    text = welcome_text(root=None, role="none", executor="codex")
    assert "检查环境" in text
    assert "继续已有项目" in text
    assert "1." in text
    assert "4." in text
    assert "5." not in text
    assert "Type exit or quit to leave." in text


def test_welcome_english_locale():
    text = welcome_text(root=None, role="none", executor="codex", locale="en")
    assert "Welcome to Iteris Monitor" in text
    assert "Create a new project" in text


def test_welcome_in_project_shows_path(tmp_path):
    src = tmp_path / "p.tex"
    src.write_text("x", encoding="utf-8")
    root = tmp_path / "proj"
    init_project(root, source=src)
    text = welcome_text(root=root, role="single", executor="claude")
    assert "proj" in text
    assert "iteris run" in text or "dashboard" in text


def test_menu_digit_two_creates_agent_handoff_seed():
    msg = resolve_menu_input("2", "none")
    assert msg is not None
    assert "创建" in msg or "create" in msg.lower()
    assert "确认" in msg or "confirm" in msg.lower()


def test_menu_five_is_not_a_menu_item():
    assert resolve_menu_input("5", "single") == "5"


def test_menu_count():
    assert len(menu_for_role("none")) == 4
    assert len(menu_for_role("single")) == 4

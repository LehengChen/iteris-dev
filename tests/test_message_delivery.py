"""Composer-delivery check must read the LIVE input line, not the
whole 40-line pane tail (which false-fires on scrollback / `message list` echoes).
"""

from __future__ import annotations

import iteris.commands.message as message
from iteris.commands.message import _composer_holds, _composer_region

MSG = "msg-20260613T000000Z-operator-hint"


def _set_pane(monkeypatch, pane: str) -> None:
    monkeypatch.setattr(message, "capture_pane", lambda *_a, **_k: pane)


def test_submitted_notice_in_scrollback_is_not_held(monkeypatch):
    # The notice was submitted: it sits in history ABOVE the (empty) composer,
    # and the agent's own `message list` echoed the id too. Old code reported
    # delivered:false here; new code must report not-held.
    pane = "\n".join(
        [
            f"  [iteris message] Unread high-priority hint {MSG} in the project inbox: run ...",
            f"  ● iteris tool message list . --unread --json   (echoes {MSG})",
            "────────────────────────────────────────────",
            "❯ ",
            "────────────────────────────────────────────",
            "  ⏵⏵ bypass permissions on · 1 shell · ↓ to manage",
        ]
    )
    _set_pane(monkeypatch, pane)
    assert _composer_holds("sess", MSG) is False


def test_unsubmitted_notice_on_live_input_line_is_held(monkeypatch):
    # Enter was swallowed: the notice is still in the live composer.
    pane = "\n".join(
        [
            "  ...prior output...",
            "────────────────────────────────────────────",
            f"❯ [iteris message] Unread high-priority hint {MSG} in the project inbox: run ...",
            "────────────────────────────────────────────",
            "  ⏵⏵ bypass permissions on · 1 shell · ↓ to manage",
        ]
    )
    _set_pane(monkeypatch, pane)
    assert _composer_holds("sess", MSG) is True


def test_codex_style_composer_marker(monkeypatch):
    # Codex-style input box with a different leader glyph; still scoped to the
    # live line. Submitted (empty composer) -> not held.
    pane = "\n".join(
        [
            f"  [iteris message] ... {MSG} ... (in scrollback)",
            "> ",
            "  send a message...",
        ]
    )
    _set_pane(monkeypatch, pane)
    assert _composer_holds("sess", MSG) is False


def test_unlocatable_composer_is_not_reported_held(monkeypatch):
    # No input-box marker (rendering quirk): do not claim "still held" -> no
    # spurious retry-Enter.
    _set_pane(monkeypatch, "garbled pane with no input box marker\nmore noise")
    assert _composer_holds("sess", MSG) is False
    assert _composer_region("no marker here") is None


def test_capture_failure_is_not_reported_held(monkeypatch):
    def _boom(*_a, **_k):
        raise RuntimeError("no pane")

    monkeypatch.setattr(message, "capture_pane", _boom)
    assert _composer_holds("sess", MSG) is False

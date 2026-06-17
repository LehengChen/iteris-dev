"""Prompt resource loading for iteris monitor."""

from __future__ import annotations

from iteris.guide.locale import normalize_locale, opening_message
from iteris.guide.paths import read_package_text


def monitor_prompt_text(locale: str, name: str) -> str:
    lang = normalize_locale(locale) or "zh"
    return read_package_text(f"prompts/monitor/{lang}/{name}.md").strip()


def monitor_system(locale: str) -> str:
    return monitor_prompt_text(locale, "system")


def monitor_session_mode(locale: str) -> str:
    return monitor_prompt_text(locale, "session_mode")


def monitor_handoff_footer(locale: str) -> str:
    return monitor_prompt_text(locale, "handoff_footer")


def opening_user_message(*, locale: str = "zh", in_project: bool, role: str | None) -> str:
    if not in_project:
        return opening_message(locale, "none")
    return opening_message(locale, role)

"""Localized resources for iteris monitor."""

from __future__ import annotations

import json
import os
import sys
from functools import lru_cache
from typing import Any

from iteris import log
from iteris.guide.paths import package_data_path

SUPPORTED_LOCALES = ("zh", "en")
WIZARD_ACTION = "__wizard__"


def normalize_locale(value: str | None) -> str | None:
    if not value:
        return None
    text = value.strip().lower()
    if text in {"zh", "zh-cn", "cn", "chinese", "中文", "1"}:
        return "zh"
    if text in {"en", "english", "2"}:
        return "en"
    return None


@lru_cache(maxsize=None)
def locale_resource(locale: str) -> dict[str, Any]:
    lang = locale if locale in SUPPORTED_LOCALES else "zh"
    path = package_data_path(f"locales/{lang}.json")
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"locale resource must be an object: {path}")
    return data


def locale_strings(locale: str) -> dict[str, Any]:
    strings = locale_resource(locale).get("strings")
    if not isinstance(strings, dict):
        raise ValueError(f"locale resource has no strings object: {locale}")
    return strings


def t(locale: str, key: str, **kwargs: Any) -> Any:
    lang = locale if locale in SUPPORTED_LOCALES else "zh"
    strings = locale_strings(lang)
    fallback = locale_strings("en")
    template = strings.get(key, fallback.get(key, key))
    if isinstance(template, str) and kwargs:
        return template.format(**kwargs)
    return template


def menu_resource(locale: str) -> dict[str, list[tuple[str, str, str | None]]]:
    raw = locale_resource(locale).get("menu")
    if not isinstance(raw, dict):
        raise ValueError(f"locale resource has no menu object: {locale}")
    menu: dict[str, list[tuple[str, str, str | None]]] = {}
    for role, items in raw.items():
        if not isinstance(items, list):
            raise ValueError(f"menu role must be a list: {locale}.{role}")
        role_items: list[tuple[str, str, str | None]] = []
        for item in items:
            if not (isinstance(item, list) and len(item) == 3):
                raise ValueError(f"menu item must be [key, title, message]: {locale}.{role}")
            key, title, message = item
            if not isinstance(key, str) or not isinstance(title, str):
                raise ValueError(f"menu key/title must be strings: {locale}.{role}")
            if message is not None and not isinstance(message, str):
                raise ValueError(f"menu message must be string or null: {locale}.{role}.{key}")
            role_items.append((key, title, message))
        menu[str(role)] = role_items
    return menu


MENU = {locale: menu_resource(locale) for locale in SUPPORTED_LOCALES}


def opening_message(locale: str, role: str | None) -> str:
    lang = locale if locale in SUPPORTED_LOCALES else "zh"
    messages = locale_resource(lang).get("opening_messages")
    if not isinstance(messages, dict):
        raise ValueError(f"locale resource has no opening_messages object: {locale}")
    key = role if role in {"single", "family_root", "family_child"} else "none"
    value = messages.get(key)
    if not isinstance(value, str):
        raise ValueError(f"missing opening message: {locale}.{key}")
    return value


def validate_locale_resources() -> None:
    baseline = locale_resource("en")
    base_string_keys = set((baseline.get("strings") or {}).keys())
    base_roles = set((baseline.get("menu") or {}).keys())
    base_opening = set((baseline.get("opening_messages") or {}).keys())
    for locale in SUPPORTED_LOCALES:
        data = locale_resource(locale)
        strings = data.get("strings") or {}
        menu = data.get("menu") or {}
        opening = data.get("opening_messages") or {}
        if set(strings.keys()) != base_string_keys:
            missing = sorted(base_string_keys - set(strings.keys()))
            extra = sorted(set(strings.keys()) - base_string_keys)
            raise ValueError(f"{locale} strings mismatch; missing={missing}; extra={extra}")
        if set(menu.keys()) != base_roles:
            raise ValueError(f"{locale} menu roles mismatch")
        if set(opening.keys()) != base_opening:
            raise ValueError(f"{locale} opening message roles mismatch")
        for role, base_items in (baseline.get("menu") or {}).items():
            items = menu.get(role) or []
            if len(items) != len(base_items):
                raise ValueError(f"{locale}.{role} menu item count mismatch")
            if [item[0] for item in items] != [item[0] for item in base_items]:
                raise ValueError(f"{locale}.{role} menu keys mismatch")


def choose_locale(explicit: str | None, *, skip_prompt: bool = False) -> str:
    normalized = normalize_locale(explicit)
    if normalized:
        return normalized
    env = normalize_locale(os.environ.get("ITERIS_MONITOR_LANG"))
    if env:
        return env
    if skip_prompt or not sys.stdin.isatty():
        return "zh"
    log.panel(
        "\n".join(
            [
                t("zh", "lang_prompt"),
                "",
                t("zh", "lang_option_zh"),
                t("zh", "lang_option_en"),
            ]
        ),
        title=t("zh", "choose_title"),
    )
    while True:
        try:
            choice = input(t("zh", "lang_input")).strip().lower()
        except (EOFError, KeyboardInterrupt):
            return "zh"
        picked = normalize_locale(choice)
        if picked:
            return picked
        log.info(t("zh", "lang_invalid"))

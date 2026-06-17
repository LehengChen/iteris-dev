"""Deterministic welcome screens and menu routing for iteris monitor."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from iteris.guide.index import ROLE_FAMILY_CHILD, ROLE_FAMILY_ROOT, ROLE_SINGLE, read_project_index
from iteris.guide.locale import MENU, WIZARD_ACTION, t

MenuItem = tuple[str, str, str | None]  # key, title, handoff seed (None => legacy local wizard)


def _role_key(role: str) -> str:
    if role == ROLE_FAMILY_ROOT:
        return "family_root"
    if role == ROLE_FAMILY_CHILD:
        return "family_child"
    if role == ROLE_SINGLE:
        return "single"
    return "none"


def menu_for_role(role: str, locale: str = "zh") -> list[MenuItem]:
    lang = locale if locale in MENU else "zh"
    key = _role_key(role)
    return list(MENU[lang].get(key) or MENU["zh"][key])


def _project_header(root: Path, locale: str) -> str:
    index = read_project_index(root)
    title = index.get("title") or root.name
    lines = [
        f"{t(locale, 'project_label')}：{title}",
        f"{t(locale, 'path_label')}：{root}",
    ]
    if index.get("source_file"):
        lines.append(f"{t(locale, 'source_label')}：{index['source_file']}")
    if index.get("target_artifact"):
        lines.append(f"{t(locale, 'target_label')}：{index['target_artifact']}")
    if index.get("role") == ROLE_FAMILY_ROOT and index.get("evolve", {}).get("goal"):
        lines.append(f"{t(locale, 'evolve_goal_label')}：{index['evolve']['goal']}")
    return "\n".join(lines)


def welcome_text(*, root: Path | None, role: str, executor: str, locale: str = "zh") -> str:
    menu = menu_for_role(role, locale)
    lines: list[str] = [t(locale, "welcome_title"), ""]

    if root is None or role == "none":
        lines.extend(
            [
                t(locale, "outside_hint"),
                "",
                t(locale, "first_use_heading"),
                f"  {t(locale, 'outside_quick_help')}",
                "",
            ]
        )
    else:
        lines.extend(
            [
                _project_header(root, locale),
                f"{t(locale, 'executor_label')}：{executor} {t(locale, 'executor_env_hint')}",
                "",
                t(locale, "in_project_hint"),
                "",
            ]
        )

    lines.append(t(locale, "menu_heading"))
    for key, title, _ in menu:
        lines.append(f"  {key}. {title}")
    lines.extend(["", t(locale, "menu_footer")])
    return "\n".join(lines)


def resolve_menu_input(text: str, role: str, locale: str = "zh") -> str | None:
    """Map menu digit to a handoff message, WIZARD_ACTION, or free text."""
    stripped = text.strip()
    if not stripped:
        return None
    menu = menu_for_role(role, locale)
    by_key = {key: msg for key, _title, msg in menu}
    if stripped not in by_key:
        return stripped
    msg = by_key[stripped]
    if msg is None:
        return WIZARD_ACTION
    if msg == "":
        return None
    return msg


def welcome_payload(*, root: Path | None, role: str, executor: str, locale: str = "zh") -> dict[str, Any]:
    return {
        "welcome": welcome_text(root=root, role=role, executor=executor, locale=locale),
        "role": role,
        "locale": locale,
        "menu": [{"key": k, "title": title} for k, title, _ in menu_for_role(role, locale)],
        "executor": executor,
    }

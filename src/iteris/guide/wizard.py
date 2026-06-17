"""Interactive guided new-project wizard for iteris monitor."""

from __future__ import annotations

import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import typer

from iteris import log
from iteris.commands.new import perform_new_project
from iteris.guide.index import ensure_project_guide_files, refresh_project_index
from iteris.guide.locale import t
from iteris.tools.arxiv import fetch_arxiv_reference, normalize_arxiv_id


def _prompt_line(label: str, *, default: str = "", locale: str = "zh") -> str:
    suffix = f" [{default}]" if default else ""
    try:
        raw = input(f"{label}{suffix} > ").strip()
    except (EOFError, KeyboardInterrupt):
        typer.echo("")
        raise typer.Exit(0) from None
    if not raw and default:
        return default
    return raw


def _yes_no(label: str, *, default: bool = False, locale: str = "zh") -> bool:
    hint = "Y/n" if default else "y/N"
    raw = _prompt_line(f"{label} ({hint})", locale=locale).lower()
    if not raw:
        return default
    return raw in {"y", "yes", "是", "好", "1"}


def _parse_paths(text: str) -> list[Path]:
    if not text.strip():
        return []
    parts = re.split(r"[,;\s]+", text.strip())
    return [Path(part).expanduser() for part in parts if part.strip()]


def _parse_arxiv_ids(text: str) -> list[str]:
    if not text.strip():
        return []
    parts = re.split(r"[,;\s]+", text.strip())
    ids: list[str] = []
    for part in parts:
        if part.strip():
            ids.append(normalize_arxiv_id(part))
    return ids


def _wizard_strings(locale: str) -> dict[str, str]:
    keys = {
        "title": "wizard_title",
        "intro": "wizard_intro",
        "dir": "wizard_dir",
        "problem": "wizard_problem",
        "paste_hint": "wizard_paste_hint",
        "refs": "wizard_refs",
        "arxiv": "wizard_arxiv",
        "notes": "wizard_notes",
        "run_now": "wizard_run_now",
        "confirm_create": "wizard_confirm_create",
        "creating": "wizard_creating",
        "fetch_arxiv": "wizard_fetch_arxiv",
        "done_title": "wizard_done_title",
        "run_started": "wizard_run_started",
        "run_skipped": "wizard_run_skipped",
        "cancelled": "wizard_cancelled",
        "error": "wizard_error",
    }
    return {key: str(t(locale, resource_key)) for key, resource_key in keys.items()}


def _looks_like_inline_problem(text: str) -> bool:
    """Heuristic: pasted problem text rather than a filesystem path."""
    if "\n" in text:
        return True
    if len(text) > 200:
        return True
    if any(ch in text for ch in (" ", "\t")) and not text.startswith((".", "/", "~")):
        return True
    if not _looks_like_path_text(text):
        if re.fullmatch(r"[A-Za-z0-9_.-]+", text.strip()):
            return False
        return True
    return False


def _looks_like_path_text(text: str) -> bool:
    stripped = text.strip()
    if not stripped:
        return False
    if stripped.startswith((".", "/", "~")):
        return True
    if "/" in stripped or "\\" in stripped:
        return True
    if re.match(r"^[A-Za-z]:[\\/]", stripped):
        return True
    return bool(Path(stripped).suffix)


def _write_problem_tex(cwd: Path, body: str) -> Path:
    target = cwd / "problem.tex"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(body.strip() + "\n", encoding="utf-8")
    return target.resolve()


def _resolve_source_path(cwd: Path, problem_input: str, *, locale: str, strings: dict[str, str]) -> Path:
    text = problem_input.strip()
    if not text:
        log.info(strings["paste_hint"])
        lines: list[str] = []
        while True:
            try:
                line = input()
            except (EOFError, KeyboardInterrupt):
                typer.echo("")
                raise typer.Exit(0) from None
            if line.strip() == "" and lines:
                break
            lines.append(line)
        body = "\n".join(lines).strip()
        if not body:
            raise typer.BadParameter("problem statement is required")
        return _write_problem_tex(cwd, body)

    candidate = Path(text).expanduser()
    try:
        if candidate.exists():
            return candidate.resolve()
    except OSError:
        return _write_problem_tex(cwd, text)

    if _looks_like_inline_problem(text):
        return _write_problem_tex(cwd, text)

    raise typer.BadParameter(f"source file not found: {candidate}")


def _launch_project_run(root: Path, executor: str | None) -> bool:
    """Start iteris run through the CLI (Typer commands cannot be called directly)."""
    iteris_bin = shutil.which("iteris")
    if iteris_bin:
        cmd = [iteris_bin, "run", str(root)]
    else:
        cmd = [sys.executable, "-m", "iteris.cli", "run", str(root)]
    if executor:
        cmd.extend(["-e", executor])
    result = subprocess.run(cmd, check=False)
    return result.returncode == 0


def _append_operator_notes(root: Path, notes: str) -> None:
    notes = notes.strip()
    if not notes:
        return
    for rel in ("docs/OPERATOR.md", ".iteris/OPERATOR.md"):
        path = root / rel
        if not path.exists():
            continue
        existing = path.read_text(encoding="utf-8")
        block = f"\n\n## Monitor wizard notes\n\n{notes.strip()}\n"
        path.write_text(existing.rstrip() + block, encoding="utf-8")


def run_new_project_wizard(*, cwd: Path, locale: str = "zh", executor: str | None = None) -> dict[str, Any] | None:
    """Run interactive wizard; returns project payload or None if cancelled."""
    strings = _wizard_strings(locale)
    log.panel(strings["intro"], title=strings["title"])

    default_dir = "./MyProblem" if locale == "zh" else "./MyProblem"
    dir_text = _prompt_line(strings["dir"], default=default_dir, locale=locale)
    root = (cwd / dir_text).resolve()

    problem_input = _prompt_line(strings["problem"], locale=locale)
    try:
        source_path = _resolve_source_path(cwd, problem_input, locale=locale, strings=strings)
    except typer.BadParameter as exc:
        log.error(str(exc))
        return None

    refs_text = _prompt_line(strings["refs"], locale=locale)
    reference_paths = _parse_paths(refs_text)
    for path in reference_paths:
        if not path.exists():
            log.error(f"reference path not found: {path}")
            return None

    arxiv_text = _prompt_line(strings["arxiv"], locale=locale)
    arxiv_ids = _parse_arxiv_ids(arxiv_text)

    notes = _prompt_line(strings["notes"], locale=locale)
    start_run = _yes_no(strings["run_now"], default=False, locale=locale)
    if not _yes_no(strings["confirm_create"], default=True, locale=locale):
        log.info(strings["cancelled"])
        return None

    log.info(strings["creating"])
    try:
        payload = perform_new_project(
            root,
            source=source_path,
            references=reference_paths,
            allow_non_empty=True,
        )
    except (typer.BadParameter, ValueError) as exc:
        log.error(strings["error"].format(error=exc))
        return None

    refresh_project_index(root)
    ensure_project_guide_files(root)
    _append_operator_notes(root, notes)

    for arxiv_id in arxiv_ids:
        log.info(strings["fetch_arxiv"].format(arxiv_id=arxiv_id))
        try:
            fetch_arxiv_reference(root, arxiv_id=arxiv_id)
        except (ValueError, OSError) as exc:
            log.warn(f"arXiv {arxiv_id}: {exc}")

    result_lines = [
        f"Project: {payload['project_path']}",
        f"Source: {payload.get('source')}",
        f"Target: {payload['target_artifact']}",
    ]
    if start_run:
        try:
            if _launch_project_run(root, executor):
                result_lines.append("")
                result_lines.append(strings["run_started"])
            else:
                result_lines.append("")
                result_lines.append(strings["run_skipped"].format(path=root))
        except OSError as exc:
            log.warn(str(exc))
            result_lines.append("")
            result_lines.append(strings["run_skipped"].format(path=root))
    else:
        result_lines.append("")
        result_lines.append(strings["run_skipped"].format(path=root))

    log.panel("\n".join(result_lines), title=strings["done_title"])
    payload["wizard"] = {"started_run": start_run, "arxiv_ids": arxiv_ids, "notes": notes.strip() or None}
    return payload

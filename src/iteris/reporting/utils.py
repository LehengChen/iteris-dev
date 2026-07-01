"""Small shared helpers for report state, evidence, and rendering."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any


def parse_status(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    data: dict[str, Any] = {}
    current_list: str | None = None
    for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw.rstrip()
        if not line.strip():
            continue
        if line.startswith("  - ") and current_list:
            data.setdefault(current_list, []).append(line[4:].strip())
            continue
        current_list = None
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        key = key.strip()
        value = value.strip()
        if value:
            data[key] = value
        else:
            data[key] = []
            current_list = key
    return data


def guess_target_artifact(root: Path) -> str:
    status = parse_status(root / "STATUS.md")
    if status.get("target_artifact"):
        return relative_project_path(root, str(status["target_artifact"]))
    result_dir = root / "results"
    candidates = sorted(
        result_dir.glob("**/*.md"),
        key=lambda path: path.stat().st_mtime if path.exists() else 0,
        reverse=True,
    )
    return str(candidates[0].relative_to(root)) if candidates else ""


def natural_proof_path(root: Path, target_artifact: str) -> str:
    target_artifact = relative_project_path(root, target_artifact)
    if not target_artifact:
        return ""
    target = root / target_artifact
    if not target.parent.exists():
        return ""
    for candidate in sorted(target.parent.glob("*natural*proof*.md")):
        return str(candidate.relative_to(root))
    return ""


def read_project_text(root: Path, rel: str | None, *, limit: int) -> str:
    rel = relative_project_path(root, rel or "")
    if not rel:
        return ""
    path = root / rel
    if not path.exists() or not path.is_file():
        return ""
    return path.read_text(encoding="utf-8", errors="replace")[:limit]


def extract_section(text: str, name: str) -> str:
    if not text:
        return ""
    pattern = re.compile(rf"^##\s+{re.escape(name)}\s*$", flags=re.MULTILINE | re.IGNORECASE)
    match = pattern.search(text)
    if not match:
        return ""
    start = match.end()
    next_match = re.search(r"^##\s+", text[start:], flags=re.MULTILINE)
    end = start + next_match.start() if next_match else len(text)
    return text[start:end].strip()


def relative_project_path(root: Path, value: str) -> str:
    value = str(value or "").strip()
    if not value:
        return ""
    if value.startswith(("http://", "https://")):
        return value
    root = root.resolve()
    path = Path(value)
    if path.is_absolute():
        try:
            return str(path.resolve().relative_to(root))
        except ValueError:
            return ""
    normalized_path = Path(value.replace("\\", "/"))
    parts = [part for part in normalized_path.parts if part not in {"", "."}]
    if not parts or any(part == ".." for part in parts):
        return ""
    return "/".join(parts)


def ordered_unique(values: list[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for value in values:
        if value and value not in seen:
            out.append(value)
            seen.add(value)
    return out

"""Environment checks shared by doctor and monitor."""

from __future__ import annotations

import os
import platform
import shutil
import subprocess
import sys
from typing import Any

_MIN_NODE_MAJOR = 18


def _has(binary: str) -> str:
    return shutil.which(binary) or ""


def _node_major(node_exe: str) -> int | None:
    try:
        out = subprocess.run([node_exe, "--version"], capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.SubprocessError):
        return None
    raw = (out.stdout or "").strip().lstrip("v")
    try:
        return int(raw.split(".", 1)[0])
    except (ValueError, IndexError):
        return None


def check_environment() -> dict[str, Any]:
    """Return system checks and readiness flags for doctor/monitor."""
    checks: list[dict[str, str]] = [
        {"name": "python", "status": "ok" if sys.version_info >= (3, 10) else "error", "detail": f"{sys.version_info.major}.{sys.version_info.minor}"},
    ]
    hints: list[str] = []
    ready = sys.version_info >= (3, 10)

    for binary in ["git", "rg", "tmux", "node", "npm"]:
        path = _has(binary)
        status = "ok" if path else ("warning" if binary in {"tmux", "node", "npm"} else "error")
        detail = path or "not found"
        if binary == "node" and path:
            major = _node_major(path)
            if major is not None and major < _MIN_NODE_MAJOR:
                status = "warning"
                detail = f"{path}; v{major} < {_MIN_NODE_MAJOR}"
        checks.append({"name": binary, "status": status, "detail": detail})
        if not path and binary in {"git", "rg"}:
            ready = False

    codex_path = _has("codex")
    claude_path = _has("claude")
    for name, path in (("codex", codex_path), ("claude", claude_path)):
        if path:
            checks.append({"name": name, "status": "ok", "detail": path})
        else:
            checks.append({"name": name, "status": "warning", "detail": "not found"})
            hints.append(f"Install {name} CLI or set ITERIS_EXECUTOR to the other executor.")

    has_executor = bool(codex_path or claude_path)
    if not has_executor:
        ready = False
        hints.append("Install at least one agent CLI (codex or claude) before using iteris monitor.")

    api_key = os.environ.get("OPENAI_API_KEY")
    checks.append(
        {
            "name": "OPENAI_API_KEY",
            "status": "ok" if api_key else "skipped",
            "detail": "set" if api_key else "not set; ok if Codex CLI login is configured",
        }
    )

    return {
        "schema_version": "iteris.environment.v0",
        "platform": platform.system(),
        "checks": checks,
        "hints": hints,
        "ready_for_monitor": ready and has_executor,
        "has_executor": has_executor,
        "codex": bool(codex_path),
        "claude": bool(claude_path),
    }

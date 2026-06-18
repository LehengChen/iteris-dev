"""Git helpers for Iteris project workspaces."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

from iteris.project import append_jsonl, now_iso


DEFAULT_GITIGNORE = [
    "# Iteris runtime/cache files",
    "__pycache__/",
    "*.py[cod]",
    ".pytest_cache/",
    ".ruff_cache/",
    ".mypy_cache/",
    ".iteris/goal_prompt.txt",
    ".iteris/current_run.json",
    ".iteris/locks/",
    "",
    "# Local-only logs",
    ".iteris/logs/",
    ".iteris/codex_home/",
    "",
    "# Report-local build outputs",
    "third_party_tex/",
    "reports/*/build/",
    "",
]


TRANSIENT_GIT_PATHS = {".iteris/current_run.json"}


class GitError(RuntimeError):
    pass


def run_git(project_root: Path, args: list[str], *, check: bool = True) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        ["git", *args],
        cwd=project_root,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if check and result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip() or "git command failed"
        raise GitError(detail)
    return result


def is_git_repo(project_root: Path) -> bool:
    result = run_git(project_root, ["rev-parse", "--is-inside-work-tree"], check=False)
    return result.returncode == 0 and result.stdout.strip() == "true"


def ensure_gitignore(project_root: Path) -> bool:
    path = project_root / ".gitignore"
    existing = path.read_text(encoding="utf-8", errors="replace") if path.exists() else ""
    changed = False
    lines = existing.splitlines()
    for line in DEFAULT_GITIGNORE:
        if line and line not in lines:
            lines.append(line)
            changed = True
    if not path.exists() or changed:
        text = "\n".join(lines).rstrip() + "\n"
        path.write_text(text, encoding="utf-8")
        return True
    return False


def init_git(project_root: Path, *, initial_branch: str = "main") -> dict[str, Any]:
    created = False
    if not is_git_repo(project_root):
        result = run_git(project_root, ["init", "-b", initial_branch], check=False)
        if result.returncode != 0:
            run_git(project_root, ["init"])
            run_git(project_root, ["checkout", "-B", initial_branch])
        created = True
    gitignore_changed = ensure_gitignore(project_root)
    return {"repo": True, "created": created, "gitignore_changed": gitignore_changed, "status": status(project_root)}


def status(project_root: Path) -> dict[str, Any]:
    repo = is_git_repo(project_root)
    if not repo:
        return {"repo": False, "branch": None, "dirty": None, "short": []}
    branch_result = run_git(project_root, ["branch", "--show-current"], check=False)
    short_result = run_git(project_root, ["status", "--short"], check=True)
    short = [line for line in short_result.stdout.splitlines() if line.strip()]
    return {"repo": True, "branch": branch_result.stdout.strip() or "(detached)", "dirty": bool(short), "short": short}


def ensure_identity(project_root: Path) -> dict[str, str]:
    name = run_git(project_root, ["config", "--get", "user.name"], check=False).stdout.strip()
    email = run_git(project_root, ["config", "--get", "user.email"], check=False).stdout.strip()
    if not name:
        name = "Iteris Agent"
        run_git(project_root, ["config", "user.name", name])
    if not email:
        email = "iteris@example.invalid"
        run_git(project_root, ["config", "user.email", email])
    return {"user.name": name, "user.email": email}


def checkpoint(
    project_root: Path,
    *,
    message: str,
    paths: list[str] | None = None,
    allow_agent_identity: bool = True,
) -> dict[str, Any]:
    if not is_git_repo(project_root):
        init_git(project_root)
    gitignore_changed = ensure_gitignore(project_root)
    if allow_agent_identity:
        ensure_identity(project_root)
    append_jsonl(
        project_root / "memory" / "scratch" / "events.jsonl",
        {
            "timestamp": now_iso(),
            "channel": "events",
            "record": {
                "event_type": "git_checkpoint",
                "message": message,
                "paths": paths or ["."],
            },
        },
    )
    selected_paths = paths or ["."]
    if paths and "memory/scratch/events.jsonl" not in paths:
        selected_paths = [*paths, "memory/scratch/events.jsonl"]
    if gitignore_changed and (not paths or ".gitignore" in paths):
        selected_paths = [*selected_paths, ".gitignore"]
    add_args = ["add", "--"] + selected_paths
    run_git(project_root, add_args)
    for transient in TRANSIENT_GIT_PATHS:
        run_git(project_root, ["rm", "--cached", "--ignore-unmatch", "--", transient], check=False)
    staged = run_git(project_root, ["diff", "--cached", "--name-only"], check=True).stdout.splitlines()
    if not staged:
        return {"committed": False, "reason": "nothing staged", "status": status(project_root)}
    result = run_git(project_root, ["commit", "-m", message], check=False)
    if result.returncode != 0:
        raise GitError(result.stderr.strip() or result.stdout.strip() or "git commit failed")
    commit = run_git(project_root, ["rev-parse", "--short", "HEAD"], check=True).stdout.strip()
    return {"committed": True, "commit": commit, "staged": staged, "status": status(project_root)}

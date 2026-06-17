"""Doctor command."""

from __future__ import annotations

import json
import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path

import typer

from iteris import __version__, log
from iteris.deploy import deploy_skew
from iteris.project import is_project

# `iteris dashboard` spawns the UI server via `node --import tsx`, which needs Node >= 18.
_MIN_NODE_MAJOR = 18


def _has(binary: str) -> str:
    path = shutil.which(binary)
    return path or ""


def _node_major(node_exe: str) -> int | None:
    """Return the major version of a node binary, or None if it can't be read."""
    try:
        out = subprocess.run([node_exe, "--version"], capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.SubprocessError):
        return None
    raw = (out.stdout or "").strip().lstrip("v")
    try:
        return int(raw.split(".", 1)[0])
    except (ValueError, IndexError):
        return None


def _package_manager() -> str | None:
    if platform.system() == "Darwin" and shutil.which("brew"):
        return "brew"
    for manager in ["apt-get", "dnf", "yum", "pacman"]:
        if shutil.which(manager):
            return manager
    return None


def _install_hint(binary: str, manager: str | None = None) -> str:
    manager = manager or _package_manager()
    if binary == "codex":
        return "Install Node/npm first if needed, then install Codex CLI with `npm install -g @openai/codex` and run `codex` once to complete login/authorization."
    if binary == "claude":
        return "Install Node/npm first if needed, then install Claude Code with `npm install -g @anthropic-ai/claude-code` and run `claude` once to complete login. Only needed if you run Iteris with `--executor claude` / $ITERIS_EXECUTOR=claude."
    if binary == "rg":
        package = {
            "brew": "ripgrep",
            "apt-get": "ripgrep",
            "dnf": "ripgrep",
            "yum": "ripgrep",
            "pacman": "ripgrep",
        }.get(manager or "", "ripgrep")
    elif binary == "python":
        package = {
            "brew": "python",
            "apt-get": "python3 python3-pip",
            "dnf": "python3 python3-pip",
            "yum": "python3 python3-pip",
            "pacman": "python python-pip",
        }.get(manager or "", "python3")
    elif binary == "node":
        package = {
            "brew": "node",
            "apt-get": "nodejs npm",
            "dnf": "nodejs npm",
            "yum": "nodejs npm",
            "pacman": "nodejs npm",
        }.get(manager or "", "nodejs npm")
    elif binary == "npm":
        package = {
            "brew": "node",
            "apt-get": "npm",
            "dnf": "npm",
            "yum": "npm",
            "pacman": "npm",
        }.get(manager or "", "npm")
    else:
        package = binary
    commands = {
        "brew": f"brew install {package}",
        "apt-get": f"sudo apt-get update && sudo apt-get install -y {package}",
        "dnf": f"sudo dnf install -y {package}",
        "yum": f"sudo yum install -y {package}",
        "pacman": f"sudo pacman -S --needed {package}",
    }
    return commands.get(manager or "", f"Install `{package}` with your OS package manager.")


def _check_environment() -> tuple[list[dict[str, str]], list[str], bool, bool]:
    manager = _package_manager()
    checks: list[dict[str, str]] = [
        {"name": "iteris", "status": "ok", "detail": __version__},
        {
            "name": "python",
            "status": "ok" if sys.version_info >= (3, 10) else "error",
            "detail": f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}",
        },
    ]
    hints: list[str] = []
    ready_for_foreground = sys.version_info >= (3, 10)
    ready_for_default = sys.version_info >= (3, 10)

    for binary in ["git", "rg", "tmux", "node", "npm"]:
        path = _has(binary)
        status = "ok" if path else ("warning" if binary in {"tmux", "node", "npm"} else "error")
        detail = path or "not found"
        if binary == "node" and path:
            major = _node_major(path)
            if major is not None and major < _MIN_NODE_MAJOR:
                status = "warning"
                detail = f"{path}; v{major} < {_MIN_NODE_MAJOR} (dashboard needs Node >= {_MIN_NODE_MAJOR})"
                hints.append(
                    f"node: Node {major} is too old for `iteris dashboard`. "
                    f"Install Node >= {_MIN_NODE_MAJOR} (e.g. `nvm install {_MIN_NODE_MAJOR}`)."
                )
        checks.append({"name": binary, "status": status, "detail": detail})
        if not path:
            hints.append(f"{binary}: {_install_hint(binary, manager)}")
            if binary in {"git", "rg"}:
                ready_for_foreground = False
                ready_for_default = False
            elif binary == "tmux":
                ready_for_default = False

    # Executors: Iteris runs the /goal loop and headless agents on codex OR
    # claude (selectable via --executor / $ITERIS_EXECUTOR). At least one must be
    # installed; a missing one is only a warning so the other can still run.
    codex_path = _has("codex")
    claude_path = _has("claude")
    for name, path in (("codex", codex_path), ("claude", claude_path)):
        if path:
            checks.append({"name": name, "status": "ok", "detail": f"{path}; run `{name}` once after first install to confirm login/authorization"})
        else:
            checks.append({"name": name, "status": "warning", "detail": "not found"})
            hints.append(f"{name}: {_install_hint(name, manager)}")
    if not (codex_path or claude_path):
        ready_for_foreground = False
        ready_for_default = False
        hints.append("executor: install at least one agent CLI (codex or claude); Iteris runs on either via --executor / $ITERIS_EXECUTOR.")

    api_key = os.environ.get("OPENAI_API_KEY")
    checks.append(
        {
            "name": "OPENAI_API_KEY",
            "status": "ok" if api_key else "skipped",
            "detail": "set" if api_key else "not set; ok if Codex CLI login is configured (Claude Code does not use it)",
        }
    )

    # Warn when the deployed venv is behind the source repo so a run
    # never silently executes stale code.
    skew = deploy_skew()
    dep = (str(skew.get("deployed_commit") or "")[:10]) or "unstamped"
    head = str(skew.get("source_head") or "")[:10]
    if skew["status"] == "skew":
        checks.append({"name": "deploy_commit", "status": "warning", "detail": f"deployed {dep} != source HEAD {head}; redeploy"})
        hints.append("deploy_commit: deployed venv is behind the source repo — run scripts/deploy.sh to deploy current code")
    elif skew["status"] == "ok":
        checks.append({"name": "deploy_commit", "status": "ok", "detail": f"deployed == source HEAD ({dep})"})
    else:
        checks.append({"name": "deploy_commit", "status": "skipped", "detail": f"deployed {dep}; source repo unresolved or not a git checkout"})

    return checks, hints, ready_for_default, ready_for_foreground


def _check_project(root: Path) -> dict[str, object]:
    if not is_project(root):
        return {
            "path": str(root),
            "is_project": False,
            "checks": [{"name": "iteris.toml", "status": "warning", "detail": "not an Iteris project"}],
        }
    checks = []
    for rel in [
        "iteris.toml",
        "PROJECT.md",
        "ROADMAP.md",
        "STATUS.md",
        "memory/facts/FACT_INDEX.jsonl",
        "tasks/TASK_BOARD.jsonl",
        "verification/VERIFICATION_INDEX.jsonl",
    ]:
        exists = (root / rel).exists()
        checks.append({"name": rel, "status": "ok" if exists else "error", "detail": "present" if exists else "missing"})
    fact_count = len(list((root / "memory" / "facts").glob("fact-*.md")))
    verify_count = len(list((root / "verification" / "results").glob("verify-*.json")))
    checks.append({"name": "facts", "status": "ok", "detail": f"{fact_count} fact file(s)"})
    checks.append({"name": "verification results", "status": "ok", "detail": f"{verify_count} result file(s)"})
    return {"path": str(root), "is_project": True, "checks": checks}


def doctor(
    project_path: str = typer.Argument(".", help="Optional Iteris project path."),
    json_output: bool = typer.Option(False, "--json", help="Print machine-readable JSON."),
) -> None:
    """Check local Iteris environment and project health."""
    system_checks, hints, ready_for_default, ready_for_foreground = _check_environment()
    root = Path(project_path).resolve()
    project = _check_project(root)
    all_checks = system_checks + list(project["checks"])  # type: ignore[arg-type]
    project_is_ready = bool(project["is_project"])
    ok = project_is_ready and all(str(item.get("status")) not in {"error", "missing"} for item in all_checks)
    payload = {
        "schema_version": "iteris.doctor.v0",
        "ok": ok,
        "ready_for_run": ready_for_default and ok,
        "ready_for_default_run": ready_for_default and ok,
        "ready_for_foreground_run": ready_for_foreground and ok,
        "system": {"checks": system_checks},
        "project": project,
        "hints": hints,
    }
    if json_output:
        typer.echo(json.dumps(payload, indent=2, sort_keys=True))
        return

    log.header("System")
    log.results_table([(item["name"], item["status"], item["detail"]) for item in system_checks], title="Environment")

    log.header(f"Project: {root}")
    title = "Project state" if project["is_project"] else "Project"
    log.results_table([(item["name"], item["status"], item["detail"]) for item in project["checks"]], title=title)  # type: ignore[index]
    if hints:
        log.panel("\n".join(hints), title="Install hints")

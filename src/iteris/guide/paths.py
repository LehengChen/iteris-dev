"""Paths for guide/monitor package data and project-local copies."""

from __future__ import annotations

from importlib import resources
from pathlib import Path


def package_data_path(name: str) -> Path:
    """Return a path to a shipped data file under ``iteris.data``."""
    root = resources.files("iteris.data")
    return Path(str(root / name))


def read_package_text(name: str) -> str:
    return package_data_path(name).read_text(encoding="utf-8")


def framework_operator_repo_path() -> Path:
    """Framework OPERATOR in the repo ``docs/`` tree (may differ from package copy)."""
    # iteris.data -> iteris -> src -> repo root
    repo_root = Path(__file__).resolve().parents[3]
    return repo_root / "docs" / "OPERATOR.md"


def project_index_path(root: Path) -> Path:
    return root / ".iteris" / "INDEX.md"


def project_operator_docs_path(root: Path) -> Path:
    return root / "docs" / "OPERATOR.md"


def project_operator_runtime_path(root: Path) -> Path:
    return root / ".iteris" / "OPERATOR.md"


def project_handoff_path(root: Path) -> Path:
    return root / ".iteris" / "monitor" / "handoff.md"


def local_handoff_path(cwd: Path) -> Path:
    return cwd / ".iteris" / "monitor" / "handoff.md"

"""Iteris monitor guide: INDEX, context assembly, and lookups."""

from iteris.guide.index import (
    build_project_index,
    detect_project_role,
    ensure_project_guide_files,
    read_project_index,
    refresh_project_index,
    sync_operator_copy,
    write_project_index,
)

__all__ = [
    "build_project_index",
    "detect_project_role",
    "ensure_project_guide_files",
    "read_project_index",
    "refresh_project_index",
    "sync_operator_copy",
    "write_project_index",
]

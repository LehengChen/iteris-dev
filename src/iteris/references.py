"""User-provided reference intake and manifest indexing.

`iteris new --references <dir-or-file>` copies source material into
`references/user/` and records every imported file in `references/MANIFEST.json`
so the goal loop can consult the pack without the operator spelling out its
location in the goal text.
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

from iteris.project import now_iso, read_json, write_json

MANIFEST_REL_PATH = "references/MANIFEST.json"
USER_REFERENCES_REL_DIR = "references/user"


def import_references(project_root: Path, sources: list[Path]) -> dict[str, Any]:
    """Copy reference files/directories into the project and index them.

    Each source directory is copied as `references/user/<dirname>/`; each source
    file as `references/user/<filename>`. Existing destinations are overwritten —
    references are user-supplied input, not agent-produced state.
    """
    project_root = project_root.resolve()
    user_dir = project_root / USER_REFERENCES_REL_DIR
    user_dir.mkdir(parents=True, exist_ok=True)
    imported: list[dict[str, Any]] = []
    for raw in sources:
        source = raw.expanduser().resolve()
        if not source.exists():
            raise FileNotFoundError(f"references path not found: {source}")
        dest = user_dir / source.name
        if source.is_dir():
            if dest.exists():
                shutil.rmtree(dest) if dest.is_dir() else dest.unlink()
            shutil.copytree(source, dest)
            copied_files = sorted(path for path in dest.rglob("*") if path.is_file())
        else:
            if dest.is_dir():
                shutil.rmtree(dest)
            shutil.copy2(source, dest)
            copied_files = [dest]
        imported.append(
            {
                "origin": str(source),
                "path": str(dest.relative_to(project_root)),
                "kind": "directory" if source.is_dir() else "file",
                "files": [str(path.relative_to(project_root)) for path in copied_files],
                "file_count": len(copied_files),
            }
        )
    manifest = _merge_manifest(project_root, imported)
    return {
        "manifest_path": MANIFEST_REL_PATH,
        "imported": imported,
        "total_files": sum(entry["file_count"] for entry in imported),
        "manifest_entries": len(manifest["entries"]),
    }


def _merge_manifest(project_root: Path, imported: list[dict[str, Any]]) -> dict[str, Any]:
    manifest_path = project_root / MANIFEST_REL_PATH
    existing = read_json(manifest_path, default=None)
    entries: list[dict[str, Any]] = []
    if isinstance(existing, dict) and isinstance(existing.get("entries"), list):
        replaced_paths = {entry["path"] for entry in imported}
        entries = [
            entry
            for entry in existing["entries"]
            if isinstance(entry, dict) and entry.get("path") not in replaced_paths
        ]
    entries.extend({**entry, "imported_at": now_iso()} for entry in imported)
    manifest = {
        "schema_version": "iteris.references_manifest.v0",
        "updated_at": now_iso(),
        "entries": entries,
    }
    write_json(manifest_path, manifest)
    return manifest

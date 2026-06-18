"""Prepare packaged LaTeX layout files for a report version."""

from __future__ import annotations

from importlib import resources
from pathlib import Path
from typing import Any

from iteris.project import now_iso, write_json
from iteris.reporting.templates import template_manifest

ASSET_SCHEMA = "iteris.template_assets.v0"


def prepare_template_assets(project_root: Path, version_dir: Path, template_id: str) -> dict[str, Any]:
    del project_root
    manifest = template_manifest(template_id)
    package_files = manifest.get("package_files") if isinstance(manifest.get("package_files"), list) else []
    payload: dict[str, Any] = {
        "schema_version": ASSET_SCHEMA,
        "template": template_id,
        "generated_at": now_iso(),
        "prepared": [],
        "missing": [],
    }
    for item in package_files:
        if not isinstance(item, dict) or not item.get("resource") or not item.get("file"):
            continue
        result = _copy_package_file(version_dir, item)
        payload["prepared" if result.get("ok") else "missing"].append(result)
    write_json(version_dir / "template.assets.json", payload)
    missing_required = [
        item
        for item in payload["missing"]
        if str(item.get("file") or "") in set(manifest.get("required_files") or [])
    ]
    if missing_required:
        files = ", ".join(str(item.get("file")) for item in missing_required)
        raise FileNotFoundError(f"missing required report layout file(s) for {template_id}: {files}")
    return payload


def _copy_package_file(version_dir: Path, item: dict[str, Any]) -> dict[str, Any]:
    resource_name = str(item["resource"])
    filename = str(item["file"])
    try:
        data = resources.files("iteris.reporting").joinpath(*resource_name.split("/")).read_bytes()
    except (FileNotFoundError, ModuleNotFoundError, OSError) as exc:
        return {"ok": False, "file": filename, "resource": resource_name, "error": str(exc)}
    (version_dir / filename).write_bytes(data)
    return {
        "ok": True,
        "file": filename,
        "resource": resource_name,
        "version_path": filename,
        "license": str(item.get("license") or "Apache-2.0"),
    }

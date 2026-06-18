"""Runtime preparation of non-vendored TeX template assets."""

from __future__ import annotations

import hashlib
import shutil
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from iteris.project import now_iso, write_json
from iteris.reporting.templates import template_manifest

ASSET_SCHEMA = "iteris.template_assets.v0"


def prepare_template_assets(project_root: Path, version_dir: Path, template_id: str) -> dict[str, Any]:
    manifest = template_manifest(template_id)
    assets = manifest.get("assets") if isinstance(manifest.get("assets"), list) else []
    payload: dict[str, Any] = {
        "schema_version": ASSET_SCHEMA,
        "template": template_id,
        "generated_at": now_iso(),
        "cache_dir": f"third_party_tex/{template_id}",
        "prepared": [],
        "missing": [],
    }
    if not assets:
        write_json(version_dir / "template.assets.json", payload)
        return payload

    cache_dir = project_root / "third_party_tex" / template_id
    cache_dir.mkdir(parents=True, exist_ok=True)
    for asset in assets:
        if not isinstance(asset, dict) or not asset.get("file"):
            continue
        result = _prepare_asset(cache_dir, version_dir, asset)
        payload["prepared" if result.get("ok") else "missing"].append(result)
    write_json(version_dir / "template.assets.json", payload)
    missing_required = [
        item
        for item in payload["missing"]
        if str(item.get("file") or "") in set(manifest.get("required_files") or [])
    ]
    if missing_required:
        files = ", ".join(str(item.get("file")) for item in missing_required)
        raise FileNotFoundError(f"missing required template asset(s) for {template_id}: {files}")
    return payload


def _prepare_asset(cache_dir: Path, version_dir: Path, asset: dict[str, Any]) -> dict[str, Any]:
    filename = str(asset["file"])
    cache_path = cache_dir / filename
    source = "cache"
    errors: list[str] = []
    if not cache_path.exists():
        for url in [str(item) for item in asset.get("urls") or [] if item]:
            try:
                data = _download(url)
            except OSError as exc:
                errors.append(f"{url}: {exc}")
                continue
            cache_path.write_bytes(data)
            source = url
            break
    if not cache_path.exists():
        return {"ok": False, "file": filename, "errors": errors}
    copied_to = ""
    if asset.get("copy_to_version", True):
        target = version_dir / filename
        shutil.copy2(cache_path, target)
        copied_to = str(target.relative_to(version_dir))
    return {
        "ok": True,
        "file": filename,
        "source": source,
        "sha256": _sha256(cache_path),
        "cached_path": str(cache_path.relative_to(cache_dir.parent.parent)),
        "version_path": copied_to,
    }


def _download(url: str) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": "iteris-report/0.1"})
    try:
        with urllib.request.urlopen(request, timeout=45) as response:
            data = response.read()
    except urllib.error.HTTPError as exc:
        raise OSError(f"HTTP {exc.code}") from exc
    except urllib.error.URLError as exc:
        raise OSError(str(exc.reason)) from exc
    if not data or data.lstrip().lower().startswith(b"<!doctype html"):
        raise OSError("download did not return a TeX asset")
    return data


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()

from __future__ import annotations

import json

from iteris.commands import dashboard as dashboard_command


def _write_package(pkg_dir):
    pkg_dir.mkdir()
    (pkg_dir / "package.json").write_text('{"scripts":{"build":"true"}}\n', encoding="utf-8")
    (pkg_dir / "package-lock.json").write_text('{"lockfileVersion":3}\n', encoding="utf-8")
    node_modules = pkg_dir / "node_modules"
    node_modules.mkdir()
    return node_modules / ".iteris-install-stamp"


def test_install_if_needed_reinstalls_legacy_stamp_with_npm_ci(tmp_path, monkeypatch):
    pkg_dir = tmp_path / "client"
    stamp = _write_package(pkg_dir)
    stamp.write_text(json.dumps({"installed": True}), encoding="utf-8")
    calls = []

    def fake_run(command, **kwargs):
        calls.append((command, kwargs))

    monkeypatch.setattr(dashboard_command.subprocess, "run", fake_run)

    dashboard_command._install_if_needed(pkg_dir, "client", {})

    assert calls
    assert calls[0][0] == ["npm", "ci"]
    payload = json.loads(stamp.read_text(encoding="utf-8"))
    assert payload["manifest_fingerprint"]
    assert payload["installer"] == "ci"


def test_install_if_needed_skips_matching_manifest_fingerprint(tmp_path, monkeypatch):
    pkg_dir = tmp_path / "client"
    stamp = _write_package(pkg_dir)
    fingerprint = dashboard_command._manifest_fingerprint([pkg_dir / "package.json", pkg_dir / "package-lock.json"])
    stamp.write_text(json.dumps({"manifest_fingerprint": fingerprint, "installer": "ci"}), encoding="utf-8")
    calls = []
    monkeypatch.setattr(dashboard_command.subprocess, "run", lambda *args, **kwargs: calls.append((args, kwargs)))

    dashboard_command._install_if_needed(pkg_dir, "client", {})

    assert calls == []

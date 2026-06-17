"""Deploy-skew detection."""

from __future__ import annotations

from pathlib import Path

import iteris.deploy as deploy


def test_deploy_skew_ok_when_deployed_matches_head(monkeypatch):
    monkeypatch.setattr(deploy, "deployed_commit", lambda: "abc1234567")
    monkeypatch.setattr(deploy, "source_repo_path", lambda: Path("/repo"))
    monkeypatch.setattr(deploy, "source_head", lambda repo=None: "abc1234567")
    info = deploy.deploy_skew()
    assert info["status"] == "ok"
    assert deploy.skew_warning() is None


def test_deploy_skew_detected_when_source_moved(monkeypatch):
    monkeypatch.setattr(deploy, "deployed_commit", lambda: "old1111111")
    monkeypatch.setattr(deploy, "source_repo_path", lambda: Path("/repo"))
    monkeypatch.setattr(deploy, "source_head", lambda repo=None: "new2222222")
    # don't shell out to git for the ancestor check
    monkeypatch.setattr(deploy.subprocess, "run", lambda *a, **k: type("R", (), {"returncode": 0})())
    info = deploy.deploy_skew()
    assert info["status"] == "skew"
    assert info["deployed_commit"] == "old1111111"
    assert info["source_head"] == "new2222222"
    assert info["deployed_is_ancestor_of_head"] is True
    warning = deploy.skew_warning()
    assert warning is not None
    assert "old1111111"[:10] in warning
    assert "new2222222"[:10] in warning


def test_deploy_skew_unknown_when_unstamped(monkeypatch):
    monkeypatch.setattr(deploy, "deployed_commit", lambda: None)
    monkeypatch.setattr(deploy, "source_repo_path", lambda: None)
    monkeypatch.setattr(deploy, "source_head", lambda repo=None: None)
    info = deploy.deploy_skew()
    assert info["status"] == "unknown"
    assert deploy.skew_warning() is None

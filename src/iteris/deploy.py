"""Deploy-skew detection.

A from-source ``pip install <repo>`` does not by itself record which git commit
was deployed, so after the source advances the venv can silently run stale code
(observed: runs launched on a pre-fix binary). We stamp the built commit into
``_build_info.py`` at deploy time and resolve the source repo from pip's
``direct_url.json`` so ``iteris --version``, ``iteris doctor``, and the
``iteris run`` preflight can flag deployed-vs-source skew.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path


def deployed_commit() -> str | None:
    """The git commit stamped into this installed copy at deploy time, or None."""
    try:
        from iteris._build_info import BUILD_COMMIT
    except Exception:
        return None
    if isinstance(BUILD_COMMIT, str) and BUILD_COMMIT.strip() and BUILD_COMMIT != "unknown":
        return BUILD_COMMIT.strip()
    return None


def source_repo_path() -> Path | None:
    """Local source repo this iteris was installed from (pip direct_url.json)."""
    try:
        raw = _distribution_text("direct_url.json")
        if not raw:
            return None
        url = json.loads(raw).get("url", "")
    except Exception:
        return None
    prefix = "file://"
    if isinstance(url, str) and url.startswith(prefix):
        return Path(url[len(prefix):])
    return None


def _distribution_text(name: str) -> str | None:
    try:
        from importlib.metadata import distribution

        return distribution("iteris").read_text(name)
    except Exception:
        return None


def _git(repo: Path, *args: str) -> str | None:
    try:
        out = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, timeout=5)
    except Exception:
        return None
    return out.stdout.strip() if out.returncode == 0 else None


def source_head(repo: Path | None = None) -> str | None:
    repo = repo or source_repo_path()
    if repo is None:
        return None
    return _git(repo, "rev-parse", "HEAD")


def deploy_skew() -> dict[str, object]:
    """Compare the deployed build commit to the source repo HEAD.

    ``status`` is ``ok`` (match), ``skew`` (deployed != HEAD), or ``unknown``
    (commit unstamped, or source repo unresolvable / not a git checkout).
    """
    deployed = deployed_commit()
    repo = source_repo_path()
    head = source_head(repo) if repo else None
    if deployed is None or head is None:
        status = "unknown"
    elif deployed == head:
        status = "ok"
    else:
        status = "skew"
    behind: bool | None = None
    if status == "skew" and repo is not None and deployed and head:
        try:
            anc = subprocess.run(
                ["git", "-C", str(repo), "merge-base", "--is-ancestor", deployed, head],
                capture_output=True,
                timeout=5,
            )
            behind = anc.returncode == 0
        except Exception:
            behind = None
    return {
        "status": status,
        "deployed_commit": deployed,
        "source_head": head,
        "source_repo": str(repo) if repo else None,
        "deployed_is_ancestor_of_head": behind,
    }


def skew_warning() -> str | None:
    """A one-line human warning when the deployed code is stale, else None."""
    info = deploy_skew()
    if info["status"] != "skew":
        return None
    dep = str(info["deployed_commit"] or "?")[:10]
    head = str(info["source_head"] or "?")[:10]
    tail = " (source moved forward; redeploy)" if info["deployed_is_ancestor_of_head"] else ""
    return f"deployed commit {dep} != source HEAD {head}{tail} — run scripts/deploy.sh to deploy current code"

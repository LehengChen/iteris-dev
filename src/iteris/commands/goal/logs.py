"""Goal-run log paths, run pruning, and latest-log lookup."""

from __future__ import annotations

import shutil
import subprocess

from iteris.project import now_stamp, read_json, slugify
from pathlib import Path


def build_goal_log_paths(root: Path, session_name: str, stamp: str | None = None) -> dict[str, Path]:
    safe_session = slugify(session_name, 60)
    stamp = stamp or now_stamp()
    logs_dir = root / ".iteris" / "logs"
    base = logs_dir / f"goal-{safe_session}-{stamp}"
    return {"pane_log": base.with_suffix(".pane.log"), "meta": base.with_suffix(".meta.json")}


def _mtime_or_zero(path: Path) -> float:
    try:
        return path.stat().st_mtime
    except OSError:
        return 0.0


def _live_goal_session_slugs() -> set[str]:
    """Slugified names of currently-live tmux sessions (empty set if no tmux)."""
    if shutil.which("tmux") is None:
        return set()
    try:
        out = subprocess.run(["tmux", "ls", "-F", "#S"], capture_output=True, text=True, timeout=5, check=False)
    except (OSError, subprocess.SubprocessError):
        return set()
    if out.returncode != 0:
        return set()
    return {slugify(name.strip(), 60) for name in out.stdout.splitlines() if name.strip()}


def prune_goal_runs(root: Path, *, keep: int = 10) -> None:
    """Bound per-run goal artifacts: keep the newest `keep` CODEX_HOME dirs and
    pane/meta log pairs, delete the rest. Rollouts and pane logs grow large and
    accumulate one set per `iteris run`, so this runs at launch time.

    Artifacts belonging to a live tmux goal session are never pruned: the run
    dir's mtime reflects creation, not ongoing rollout writes, so age alone
    would misjudge a long-lived run as stale.
    """
    live_slugs = _live_goal_session_slugs()

    def _mangle(name: str) -> str:
        # tmux rewrites '.' and ':' to '_' in session names, so a live session
        # is reported mangled while artifact names keep the original slug.
        return name.replace(".", "_").replace(":", "_")

    def _is_live(name: str) -> bool:
        return any(_mangle(name).startswith(f"goal-{_mangle(slug)}-") for slug in live_slugs)

    home_root = root / ".iteris" / "codex_home"
    if home_root.is_dir():
        run_dirs = sorted(
            (p for p in home_root.iterdir() if p.is_dir() and p.name.startswith("goal-")),
            key=_mtime_or_zero,
            reverse=True,
        )
        for old in run_dirs[keep:]:
            if _is_live(old.name):
                continue
            shutil.rmtree(old, ignore_errors=True)
    logs_dir = root / ".iteris" / "logs"
    if logs_dir.is_dir():
        pane_logs = sorted(logs_dir.glob("goal-*.pane.log"), key=_mtime_or_zero, reverse=True)
        for old in pane_logs[keep:]:
            if _is_live(old.name):
                continue
            meta = old.with_name(old.name[: -len(".pane.log")] + ".meta.json")
            for victim in (old, meta):
                try:
                    victim.unlink()
                except OSError:
                    pass


def latest_goal_logs(root: Path, session_name: str) -> dict[str, str | None]:
    logs_dir = root / ".iteris" / "logs"
    prefix = f"goal-{slugify(session_name, 60)}-"
    result: dict[str, str | None] = {"meta": None, "pane_log": None}
    metas = sorted(logs_dir.glob(f"{prefix}*.meta.json"), key=lambda path: path.stat().st_mtime, reverse=True)
    if metas:
        meta = metas[0]
        result["meta"] = str(meta)
        payload = read_json(meta, default={})
        pane_log = payload.get("pane_log") if isinstance(payload, dict) else None
        pane_path = root / str(pane_log) if pane_log else meta.with_suffix(".pane.log")
        if pane_path.exists():
            result["pane_log"] = str(pane_path)
        return result
    pane_logs = sorted(logs_dir.glob(f"{prefix}*.pane.log"), key=lambda path: path.stat().st_mtime, reverse=True)
    if pane_logs:
        result["pane_log"] = str(pane_logs[0])
    return result

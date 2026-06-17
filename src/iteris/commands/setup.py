"""Setup command."""

from __future__ import annotations

import shutil

from iteris import log


def setup() -> None:
    """Check lightweight Iteris prerequisites."""
    rows = []
    # codex/claude are the interchangeable executors; either satisfies /goal
    # launch, so each is a warning (not error) when individually missing.
    for binary in ["python3", "git", "rg", "tmux", "codex", "claude"]:
        path = shutil.which(binary)
        status = "ok" if path else ("warning" if binary in {"codex", "claude"} else "error")
        detail = path or ("optional executor; needed for /goal launch on this backend" if binary in {"codex", "claude"} else "not found")
        rows.append((binary, status, detail))
    log.results_table(rows, title="Setup checks")
    log.success("Setup check complete")


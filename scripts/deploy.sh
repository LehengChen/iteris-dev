#!/usr/bin/env bash
# Blessed lightweight Iteris code-redeploy.
#
# A plain `pip install <repo>` no-ops when the version string is unchanged (it is
# pinned at 0.1.0 across many code commits), silently leaving stale code in the
# venv. This script force-reinstalls so the deploy actually swaps the binary, and
# stamps the built git commit into the installed package so `iteris --version`,
# `iteris doctor`, and the `iteris run` preflight can report and skew-check it.
#
# Use this for a code-only redeploy from an already-set-up venv. For a fresh
# machine setup (system deps + node + venv), use install.sh.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV="${ITERIS_VENV_DIR:-$HOME/.local/share/iteris/venv}"
PY="$VENV/bin/python"
[ -x "$PY" ] || { echo "deploy: venv python not found at $PY (run install.sh first)" >&2; exit 1; }

COMMIT="$(git -C "$ROOT" rev-parse HEAD 2>/dev/null || echo unknown)"
echo "deploy: force-reinstalling iteris from $ROOT @ $COMMIT"
rm -rf "$ROOT/build" "$ROOT/dist" "$ROOT/src/iteris.egg-info"
"$PY" -m pip install --force-reinstall --no-deps "$ROOT" >/dev/null

"$PY" - "$COMMIT" <<'PYSTAMP'
import sys, pathlib, iteris
p = pathlib.Path(iteris.__file__).resolve().parent / "_build_info.py"
p.write_text('"""Deploy provenance - stamped by scripts/deploy.sh."""\n\nfrom __future__ import annotations\n\nBUILD_COMMIT = %r\n' % sys.argv[1])
print("deploy: stamped BUILD_COMMIT", sys.argv[1])
PYSTAMP

"$VENV/bin/iteris" --version

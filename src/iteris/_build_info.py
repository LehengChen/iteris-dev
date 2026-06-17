"""Deploy provenance.

``BUILD_COMMIT`` is overwritten in the INSTALLED copy by the blessed deploy path
(``scripts/deploy.sh`` / ``install.sh``) right after ``pip install``, so the
deployed venv can report exactly which source commit it is running. The in-repo
value stays ``None`` so a from-source / unstamped install degrades to
"unknown" rather than reporting a false commit.
"""

from __future__ import annotations

BUILD_COMMIT: str | None = None

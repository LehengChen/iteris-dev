"""Shared test helpers."""

from __future__ import annotations

import re

import pytest

_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")
_BORDER_RE = re.compile(r"[│╭╮╰╯─]")


def plain_output(text: str) -> str:
    """Normalize Rich CLI output so substring assertions are stable.

    Raw CLI output varies with the environment in two ways that have nothing
    to do with the behavior under test:

    * Color. Rich styles fragments of a message independently — ``--source``
      becomes ``\x1b[1;36m-\x1b[0m\x1b[1;36m-source\x1b[0m`` and a path is
      split into a styled parent and filename — so a literal substring can be
      absent whenever color is on. CI enables color; a plain local run does
      not, which is how such assertions pass locally and fail in CI.
    * Wrapping. Rich hard-wraps long lines and pads error boxes to the
      terminal width with ``│`` borders, so where a phrase breaks depends on
      the terminal width and on the length of interpolated paths (CI's
      ``tmp_path`` is longer than a local one).

    Stripping escapes and box borders, then collapsing whitespace, leaves the
    message text itself.
    """
    without_ansi = _ANSI_RE.sub("", text)
    without_borders = _BORDER_RE.sub(" ", without_ansi)
    return re.sub(r"\s+", " ", without_borders).strip()


@pytest.fixture
def plain():
    """Fixture form of :func:`plain_output`."""
    return plain_output

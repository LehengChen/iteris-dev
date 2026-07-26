"""Session names must survive tmux ``-t`` target parsing.

A project directory named ``2.15`` used to produce a session Iteris could
create but never address again: ``tmux new-session -s iteris-2.15`` silently
STORES the session as ``iteris-2_15``, while every later ``-t iteris-2.15``
is parsed as pane 15 of session ``iteris-2`` and fails with
``can't find pane: 15``. The worker stayed live but unreachable — no attach,
no pane log, no message delivery, no stop.

These tests pin both defenses: ``session_slug`` (every Iteris-generated name)
and ``tmux_target`` (names a user passes via ``--session``).
"""

from __future__ import annotations

from pathlib import Path

from iteris.project import session_slug, tmux_safe_name
from iteris.tmux import (
    build_interrupt_command,
    build_kill_session_command,
    build_pipe_pane_command,
    tmux_attach_command,
    tmux_target,
)


def test_session_slug_folds_dots_and_colons():
    # The family-closure use case: sibling ids like 2.15-2.19.
    assert session_slug("2.15") == "2_15"
    assert session_slug("2.19") == "2_19"
    assert session_slug("Problem.2.15.final") == "problem_2_15_final"
    # Colons never reach the fold: slugify already drops them to "-" because
    # they are outside its allowed set. Only "." survived slugify, which is
    # why the dot case was the one that reached tmux and broke. Pinned so a
    # future slugify that admits ":" does not silently reintroduce the bug.
    assert ":" not in session_slug("run:2")


def test_session_slug_keeps_sibling_ids_distinct():
    """Folding must not collapse the very names family closure runs together."""
    slugs = {session_slug(f"2.{n}") for n in range(15, 20)}
    assert len(slugs) == 5
    assert all("." not in slug and ":" not in slug for slug in slugs)


def test_session_slug_dot_free_names_unchanged():
    """Existing projects keep their historical session names."""
    assert session_slug("problem-3-2-claude") == "problem-3-2-claude"
    assert session_slug("MyProblem") == "myproblem"


def test_session_slug_long_dotted_name_is_addressable_and_stable():
    """Truncated+digest names must also come out dot-free and deterministic."""
    name = "iteris-gecp-evo-2.15-fixed-tolerance-dichotomy-long-tail"
    slug = session_slug(name)
    assert len(slug) <= 30
    assert "." not in slug
    assert slug == session_slug(name)


def test_tmux_safe_name_is_idempotent():
    once = tmux_safe_name("2.15")
    assert tmux_safe_name(once) == once == "2_15"


def test_tmux_target_normalizes_user_supplied_session():
    """A hand-passed --session iteris-2.15 must still address the real session."""
    assert tmux_target("iteris-2.15") == "iteris-2_15"
    assert tmux_target("iteris-2_15") == "iteris-2_15"
    assert tmux_target("plain-name") == "plain-name"


def test_tmux_command_builders_normalize_target():
    """Every -t carrying command must point at the name tmux actually stored."""
    session = "iteris-2.15"
    expected = "iteris-2_15"

    assert build_interrupt_command(session) == ["tmux", "send-keys", "-t", expected, "C-c"]
    assert build_kill_session_command(session) == ["tmux", "kill-session", "-t", expected]

    pipe = build_pipe_pane_command(session, Path("/tmp/pane.log"))
    assert pipe[:5] == ["tmux", "pipe-pane", "-o", "-t", expected]

    assert tmux_attach_command(session, env={}) == ["tmux", "attach-session", "-t", expected]
    assert tmux_attach_command(session, env={"TMUX": "/tmp/x,1,0"}) == [
        "tmux",
        "switch-client",
        "-t",
        expected,
    ]


def test_goal_and_run_builders_normalize_target():
    from iteris.commands.goal.session import (
        build_send_keys_command,
        build_submit_prompt_command,
        build_tmux_command,
        build_tmux_shell_command,
    )

    expected = "iteris-2_15"
    assert build_tmux_shell_command("iteris-2.15") == ["tmux", "new-session", "-d", "-s", expected]
    # Creation and lookup must agree, or we recreate the original bug.
    assert build_tmux_command("iteris-2.15", "sleep 1", detached=True)[4] == expected
    assert build_send_keys_command("iteris-2.15", "cmd")[3] == expected
    assert build_submit_prompt_command("iteris-2.15")[3] == expected

"""Session housekeeping: gc tool + evolve backstop reaper."""

from __future__ import annotations

import pytest

import iteris.sessions as sessions_mod
import iteris.supervision.profiles.evolve as evolve_profile
from iteris.project import init_project, slugify, write_json
from iteris.sessions import gc_sessions, reapable_sessions
from iteris.supervision.contracts import Action
from iteris.supervision.events import Observation


@pytest.fixture()
def workspace(tmp_path, monkeypatch):
    done = tmp_path / "done-proj"
    init_project(done)
    (done / "STATUS.md").write_text("phase: goal_success_verified\n", encoding="utf-8")

    busy = tmp_path / "busy-proj"
    init_project(busy)
    (busy / "STATUS.md").write_text("phase: exploring\n", encoding="utf-8")

    analyzed = tmp_path / "analyzed-proj"
    init_project(analyzed)
    (analyzed / "STATUS.md").write_text("phase: exploring\n", encoding="utf-8")
    (analyzed / "generalize").mkdir(exist_ok=True)
    write_json(analyzed / "generalize" / "analysis.json", {"directions": []})

    live = {
        f"iteris-{slugify('done-proj', 30)}",
        f"iteris-{slugify('busy-proj', 30)}",
        f"iteris-analyze-{slugify('analyzed-proj', 30)}",
        f"iteris-evolve-{slugify('done-proj', 30)}",  # master: never a candidate
        "unrelated-session",
    }
    monkeypatch.setattr(sessions_mod, "list_tmux_sessions", lambda: sorted(live))
    killed: list[str] = []
    monkeypatch.setattr(
        sessions_mod, "kill_tmux_session", lambda name: killed.append(name) or True
    )
    return tmp_path, killed


def test_reapable_and_gc(workspace):
    root, killed = workspace
    candidates = reapable_sessions(root)
    names = {(c["kind"], c["session_name"]) for c in candidates}
    assert names == {
        ("worker", f"iteris-{slugify('done-proj', 30)}"),
        ("analyze", f"iteris-analyze-{slugify('analyzed-proj', 30)}"),
    }
    # busy worker, evolve master, unrelated session untouched.

    results = gc_sessions(root, dry_run=True)
    assert killed == [] and all(r["dry_run"] for r in results)

    results = gc_sessions(root)
    assert sorted(killed) == sorted(r["session_name"] for r in results)
    assert all(r["killed"] for r in results)


def _node(**over):
    base = {
        "node_id": "n1",
        "project": "/x/proj-one",
        "verified": False,
        "reduced": False,
        "session_alive": False,
        "new_facts": [],
        "has_analysis": False,
        "analyze_session_alive": False,
    }
    base.update(over)
    # The sensor always emits ``terminal`` (goal_success OR certified
    # principled_stop); derive it here so fixtures mirror production unless a
    # test sets it explicitly.
    base.setdefault("terminal", bool(base["verified"] or base["reduced"]))
    return base


def _obs(nodes):
    return {"nodes": Observation(sensor="nodes", data={"nodes": nodes})}


def test_reap_trigger_condition_and_params():
    rule = next(
        r for r in evolve_profile.build_triggers() if r.name == "reap_finished_sessions"
    )
    # Verified + alive + quiet -> fires; still-producing node does not.
    assert rule.condition(_obs([_node(verified=True, session_alive=True)]))
    assert not rule.condition(
        _obs([_node(verified=True, session_alive=True, new_facts=[{"f": 1}])])
    )
    assert rule.condition(_obs([_node(has_analysis=True, analyze_session_alive=True)]))
    assert not rule.condition(_obs([_node()]))

    params = rule.params(
        _obs(
            [
                _node(verified=True, session_alive=True),
                _node(node_id="n2", project="/x/proj-two", has_analysis=True,
                      analyze_session_alive=True),
            ]
        )
    )
    assert params["workers"] == ["/x/proj-one"]
    assert params["analyze_sessions"] == [f"iteris-analyze-{slugify('proj-two', 30)}"]


def test_reap_actuator(monkeypatch):
    alive = {f"iteris-{slugify('proj-one', 30)}", "iteris-analyze-proj-two"}
    killed: list[str] = []
    monkeypatch.setattr(evolve_profile, "tmux_session_alive", lambda s: s in alive)
    monkeypatch.setattr(evolve_profile, "_kill_session", lambda s: killed.append(s) or True)
    action = Action(
        name="reap_sessions",
        params={
            "workers": ["/x/proj-one", "/x/proj-gone"],  # second has no live session
            "analyze_sessions": ["iteris-analyze-proj-two"],
        },
    )
    detail = evolve_profile._reap_sessions(action, ctx=None)
    assert sorted(detail["reaped"]) == sorted(alive)
    assert detail["failed"] == [] and sorted(killed) == sorted(alive)


def test_session_slug_disambiguates_truncated_names():
    from iteris.project import session_slug

    # Real collision from the GECP evolve run: two sibling projects sharing a
    # 30-char prefix got the same tmux session name, and the supervisor
    # reaped the new worker against the old verified node.
    a = session_slug("Iteris-GECP-evo-fixed-tolerance-dichotomy")
    b = session_slug("Iteris-GECP-evo-fixed-tolerance-rank-law-certify")
    assert a != b
    assert len(a) <= 30 and len(b) <= 30
    # Stable across calls, and short names keep their historical form.
    assert a == session_slug("Iteris-GECP-evo-fixed-tolerance-dichotomy")
    assert session_slug("problem-3-2-claude") == "problem-3-2-claude"

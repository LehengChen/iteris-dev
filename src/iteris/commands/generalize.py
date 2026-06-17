"""User-facing `iteris generalize` command.

Seeds a sibling Iteris project from a parent project's verified result plus a
generalization direction, then prints next-step hints. It does not launch the
goal loop (mirrors `iteris new`).
"""

from __future__ import annotations

import json
import sys
from typing import Any

import typer

from iteris import log
from iteris.generalize import (
    GeneralizeError,
    discover_verified_facts,
    read_curated_fact_ids,
    resolve_parent,
    seed_generalization,
)


def generalize(
    parent_path: str = typer.Argument(".", help="Parent Iteris project to generalize from."),
    direction: str = typer.Option(
        ...,
        "--direction",
        "-d",
        help="Path to a direction markdown file (relative to the parent or absolute), OR free text describing the direction.",
    ),
    source_result: str | None = typer.Option(
        None,
        "--source-result",
        help="Parent verified result to generalize, relative to the parent. Defaults to the parent's target_artifact.",
    ),
    target: str | None = typer.Option(
        None,
        "--target",
        "-t",
        help="New project directory. Defaults to a sibling <parent>-gen-<slug>.",
    ),
    no_input: bool = typer.Option(
        False,
        "--no-input",
        help="Skip the interactive fact selection and inherit all verified facts.",
    ),
    json_output: bool = typer.Option(False, "--json", help="Print machine-readable JSON (implies --no-input)."),
) -> None:
    """Seed a sibling project that generalizes a parent project's verified result."""
    try:
        parent_root = resolve_parent(parent_path)
    except FileNotFoundError as exc:
        raise typer.BadParameter(
            f"not an Iteris project: {parent_path}. Run `iteris new --source ...` there first."
        ) from exc

    interactive = not json_output and not no_input and sys.stdin.isatty()
    selected_fact_ids: list[str] | None = None
    if interactive:
        try:
            selected_fact_ids = _select_facts(parent_root)
        except GeneralizeError as exc:
            raise typer.BadParameter(str(exc)) from exc

    try:
        result = seed_generalization(
            parent_root,
            source_result=source_result,
            direction=direction,
            target=target,
            selected_fact_ids=selected_fact_ids,
        )
    except GeneralizeError as exc:
        raise typer.BadParameter(str(exc)) from exc

    payload = _payload(result)
    if json_output:
        typer.echo(json.dumps(payload, indent=2, ensure_ascii=False))
        return
    _print_ready(payload)


def _select_facts(parent_root) -> list[str] | None:
    """Interactively choose which verified parent facts to inherit.

    Recommends the parent's curated core chain (STATUS.md ``verified_facts:``) and
    lets the user accept it, take all verified facts, or pick an explicit subset.
    Returns ``None`` to fall back to the curated default, or an explicit id list.
    """
    facts = discover_verified_facts(parent_root)
    if not facts:
        log.warn("Parent project has no verified facts to inherit.")
        return []
    curated = set(read_curated_fact_ids(parent_root))
    rows = [
        (
            str(i + 1),
            "core" if str(f["fact_id"]) in curated else "",
            str(f["fact_id"]),
            str(f["claim_summary"])[:140],
        )
        for i, f in enumerate(facts)
    ]
    log.results_table(rows, title=f"Verified facts in {parent_root.name}")

    if curated and curated != {str(f["fact_id"]) for f in facts}:
        n_core = len([f for f in facts if str(f["fact_id"]) in curated])
        if typer.confirm(f"Inherit the {n_core} curated core facts? (recommended)", default=True):
            return None  # seed_generalization defaults to the curated chain
        if typer.confirm(f"Inherit all {len(facts)} verified facts instead?", default=False):
            return [str(f["fact_id"]) for f in facts]
    elif typer.confirm(f"Inherit all {len(facts)} verified facts?", default=True):
        return None

    raw = typer.prompt("Enter the numbers to inherit (comma-separated), or leave blank for none", default="")
    chosen: list[str] = []
    for token in raw.split(","):
        token = token.strip()
        if not token:
            continue
        try:
            idx = int(token)
        except ValueError:
            continue
        if 1 <= idx <= len(facts):
            chosen.append(str(facts[idx - 1]["fact_id"]))
    return chosen


def _payload(result) -> dict[str, Any]:
    return {
        "schema_version": "iteris.generalize.v0",
        "parent_project": str(result.parent_root),
        "child_project": str(result.child_root),
        "problem_id": result.problem_id,
        "source_result": result.source_result_rel,
        "direction": {
            "kind": result.direction.kind,
            "title": result.direction.title,
            "sources_file": result.direction.seed_filename,
        },
        "target_artifact": result.target_artifact,
        "goal": result.goal,
        "inherited_facts": result.inherited,
        "lineage": str(result.lineage_path.relative_to(result.child_root)) if result.lineage_path else None,
        "git": result.git,
        "next_commands": [f"cd {result.child_root}", "iteris run"],
    }


def _print_ready(payload: dict[str, Any]) -> None:
    direction = payload["direction"]
    lines = [
        f"Parent: {payload['parent_project']}",
        f"Source result: {payload['source_result']}",
        f"Direction: {direction['title']} ({direction['kind']})",
        f"Project: {payload['child_project']}",
        f"Target: {payload['target_artifact']}",
        f"Inherited facts: {len(payload['inherited_facts'])} (status: reviewed)",
        "",
        "Review the seeded project, then start work:",
        f"  cd {payload['child_project']}",
        "  iteris run",
        "",
        "Monitor or control the run:",
        "  iteris status",
        "  iteris monitor",
        "  iteris stop",
    ]
    log.panel("\n".join(lines), title="Generalization project ready")

"""Human-oriented Iteris command guide."""

from __future__ import annotations

import typer
from rich.table import Table

from iteris import __version__, log


TOPICS = {"workflow", "monitor", "run", "supervise", "review", "tools", "all"}


def help_command(topic: str = typer.Argument("workflow", help="Guide topic: workflow, monitor, run, supervise, review, tools, or all.")) -> None:
    """Show the practical Iteris command guide."""
    normalized = topic.lower().strip()
    if normalized not in TOPICS:
        raise typer.BadParameter(f"unknown help topic: {topic}. Use one of: {', '.join(sorted(TOPICS))}")

    log.banner(__version__)
    if normalized == "all":
        for section in ["workflow", "monitor", "run", "supervise", "review", "tools"]:
            _print_section(section)
        return
    _print_section(normalized)


def _print_section(topic: str) -> None:
    if topic == "workflow":
        log.panel(
            "\n".join(
                [
                    "Start with iteris monitor: the main human interaction entry point for Iteris.",
                    "Use it for setup, new projects, status questions, run recovery, and evolve families.",
                    "",
                    "Create a project:",
                    "  mkdir -p /path/to/Iteris-MyProblem",
                    "  cd /path/to/Iteris-MyProblem",
                    "  iteris new --source /path/to/problem.tex",
                    "",
                    "Start the work loop:",
                    "  iteris run",
                    "",
                    "Optional references:",
                    "  put papers, notes, PDFs, or source material in references/",
                    "",
                    "Interact and observe:",
                    "  iteris monitor",
                    "  iteris dashboard",
                    "  iteris status",
                    "  iteris review",
                ]
            ),
            title="Iteris workflow",
        )
        return

    if topic == "monitor":
        log.panel(
            "\n".join(
                [
                    "iteris monitor — primary human interaction entry point",
                    "",
                    "  iteris monitor",
                    "  iteris monitor /path/to/project",
                    "  iteris monitor --executor claude",
                    "",
                    "Use monitor to ask what to do next, create projects through the wizard,",
                    "understand project state, recover stalled runs, manage evolve families,",
                    "and route live log inspection to iteris dashboard.",
                ]
            ),
            title="iteris monitor",
        )
        return

    rows: list[tuple[str, str]] = {
        "run": [
            ("iteris run", "Start a detached tmux agent /goal loop with the default solve-the-source goal."),
            ("iteris run --goal \"...\"", "Start with an explicit goal."),
            ("iteris run --print", "Write .iteris/goal_prompt.txt and print the launch command without starting."),
            ("iteris run --foreground", "Run the agent in the current terminal when tmux is unavailable or undesired."),
            ("iteris run --attach", "Start and immediately attach to tmux. Detach with Ctrl-b then d."),
            ("iteris run --new-session", "Start another run when the default session is already active."),
        ],
        "supervise": [
            ("iteris monitor", "Primary human interaction entry point: setup, project creation, status, recovery, evolve, and next steps."),
            ("iteris dashboard", "Browser UI for live logs, facts, and evolve family view."),
            ("iteris status", "Show source, target, active session, facts, tasks, verification, and git state."),
            ("iteris recover", "Reconcile dead sessions and orphaned agent runs after a crash."),
            ("iteris attach", "Attach to the live tmux session. Detach with Ctrl-b then d."),
            ("iteris stop", "Stop the worker run session and related verification agents."),
        ],
        "review": [
            ("iteris review", "Create a reproducibility bundle and list the files reviewers should inspect."),
            ("STATUS.md", "Current project status."),
            ("results/<problem-id>/answer.md", "Working terminal answer artifact (verified copy answer_verified.md appears after goal-success passes)."),
            ("tasks/TASK_POOL.json", "Task frontier and completed/rejected work."),
            ("memory/facts/", "Durable fact store."),
            ("verification/results/", "Fact, assembly, and goal-success verification results."),
            ("artifacts/ARTIFACT_INDEX.jsonl", "Global append-only index of artifact workspaces and completed outputs."),
            ("artifacts/agent_runs/", "Raw subagent prompts, logs, status, and structured outputs."),
            ("artifacts/proofs/", "Proof attempts, lemma chains, and verification claims."),
            ("artifacts/experiments/", "Experiment scripts, configs, raw outputs, and reports."),
            ("artifacts/code/", "Reusable prototypes and implementation notes."),
            ("artifacts/route_checks/", "Route summaries and compatibility reports."),
            ("artifacts/<kind>/<task-label>/<run-id>/artifact_manifest.json", "Per-run artifact indexes for review and future GUI use."),
            ("artifacts/run_bundles/*/manifest.json", "Run bundle index with logs and artifact references."),
        ],
        "tools": [
            ("iteris tool context --json", "Agent-oriented project context."),
            ("iteris tool memory ...", "Fact and memory operations."),
            ("iteris tool task ...", "Task board and TASK_POOL.json operations."),
            ("iteris tool frontier refresh", "Rebuild the fact-centered route map from facts, tasks, and artifacts."),
            ("iteris tool frontier health", "Check whether the current route map recommends a fresh explore subagent."),
            ("iteris tool agent ...", "Background explore/execute subagents."),
            ("iteris tool artifact index", "Show the global artifact index and recent manifests."),
            ("iteris tool artifact gate", "Check that scripts and key artifacts are covered by manifests/index records."),
            ("iteris tool artifact search \"...\"", "Search artifact index records and manifests."),
            ("iteris tool verify ...", "Verification requests and result adoption."),
            ("iteris tool theorem ...", "Theorem search and arXiv fetch helpers."),
            ("iteris tool git checkpoint -m \"checkpoint: <summary>\"", "Operator checkpointing command used by agents."),
            ("iteris tool logs bundle", "Raw/reproducible log bundle creation."),
        ],
    }[topic]

    table = Table(title=f"iteris help {topic}", border_style="dim", padding=(0, 1))
    table.add_column("Command or file", style="bold cyan")
    table.add_column("Use")
    for command, use in rows:
        table.add_row(command, use)
    log.console.print(table)

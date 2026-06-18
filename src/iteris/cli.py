"""Iteris CLI entrypoint."""

from __future__ import annotations

from typing import Optional

import click
import typer
import typer.core
from rich.align import Align
from rich.table import Table
from rich.panel import Panel
from rich.text import Text

from iteris import __version__, log


def _quick_start_panel() -> Panel:
    table = Table.grid(padding=(0, 2))
    table.add_column(style="bold cyan", no_wrap=True)
    table.add_column()
    table.add_row("1", "Create a separate project directory")
    table.add_row(" ", "[dim]mkdir -p ./MyProblem && cd ./MyProblem[/dim]")
    table.add_row("2", "Start the interactive monitor")
    table.add_row(" ", "[bold]iteris monitor[/bold]")
    table.add_row("3", "Use manual commands only when you need them")
    table.add_row(" ", "[dim]iteris new --source ...  ·  iteris run  ·  iteris dashboard[/dim]")

    title = Text("Recommended start", style="bold")
    return Panel(
        table,
        title=title,
        subtitle="Run `iteris help all` for the full guide",
        border_style="cyan",
        padding=(1, 2),
    )


class BannerGroup(typer.core.TyperGroup):
    def format_help(self, ctx: click.Context, formatter: click.HelpFormatter) -> None:
        log.banner(__version__)
        super().format_help(ctx, formatter)
        log.console.print(Align.center(_quick_start_panel()))


app = typer.Typer(
    cls=BannerGroup,
    help="Goal-driven research agent workspace toolkit.",
    invoke_without_command=True,
    no_args_is_help=True,
    context_settings={"help_option_names": ["-h", "--help"]},
    rich_markup_mode="rich",
)


def _version_callback(value: bool) -> None:
    if value:
        log.banner(__version__)
        raise typer.Exit()


@app.callback()
def main(
    version: Optional[bool] = typer.Option(
        None,
        "--version",
        "-V",
        help="Show version and exit.",
        callback=_version_callback,
        is_eager=True,
    ),
) -> None:
    """Iteris command line."""


from iteris.commands.agent import app as agent_app  # noqa: E402
from iteris.commands.artifact import app as artifact_app  # noqa: E402
from iteris.commands.context import context  # noqa: E402
from iteris.commands.dashboard import dashboard  # noqa: E402
from iteris.commands.doctor import doctor  # noqa: E402
from iteris.commands.evolve import app as evolve_app  # noqa: E402
from iteris.commands.frontier import app as frontier_app  # noqa: E402
from iteris.commands.generalize import generalize  # noqa: E402
from iteris.commands.generalize_tool import app as generalize_tool_app  # noqa: E402
from iteris.commands.git import app as git_app  # noqa: E402
from iteris.commands.help import help_command  # noqa: E402
from iteris.commands.goal import app as goal_app  # noqa: E402
from iteris.commands.init import init  # noqa: E402
from iteris.commands.logs import app as logs_app  # noqa: E402
from iteris.commands.memory import app as memory_app  # noqa: E402
from iteris.commands.message import app as message_app  # noqa: E402
from iteris.commands.monitor import monitor  # noqa: E402
from iteris.commands.new import new  # noqa: E402
from iteris.commands.recover import recover  # noqa: E402
from iteris.commands.report import app as report_app  # noqa: E402
from iteris.commands.run import bootstrap, run  # noqa: E402
from iteris.commands.session_tool import app as session_app  # noqa: E402
from iteris.commands.setup import setup  # noqa: E402
from iteris.commands.task import app as task_app  # noqa: E402
from iteris.commands.theorem import app as theorem_app  # noqa: E402
from iteris.commands.ui import app as ui_app  # noqa: E402
from iteris.commands.verification import app as verify_app  # noqa: E402
from iteris.commands.version import version as version_cmd  # noqa: E402
from iteris.commands.workflow import attach, review, status, stop  # noqa: E402

tool_app = typer.Typer(help="Agent and operator tools used inside Iteris runs.")

app.command()(new)
app.command()(generalize)
app.command()(run)
app.command()(status)
app.command()(attach)
app.command()(stop)
app.command()(recover)
app.command()(review)
app.command()(dashboard)
app.command()(monitor)
app.command()(doctor)
app.add_typer(report_app, name="report")
app.add_typer(evolve_app, name="evolve")
app.command("help")(help_command)
app.command("version")(version_cmd)

tool_app.command()(init)
tool_app.command("bootstrap")(bootstrap)
tool_app.command()(setup)
tool_app.command()(context)
tool_app.add_typer(agent_app, name="agent")
tool_app.add_typer(artifact_app, name="artifact")
tool_app.add_typer(frontier_app, name="frontier")
tool_app.add_typer(generalize_tool_app, name="generalize")
tool_app.add_typer(git_app, name="git")
tool_app.add_typer(logs_app, name="logs")
tool_app.add_typer(memory_app, name="memory")
tool_app.add_typer(message_app, name="message")
tool_app.add_typer(session_app, name="session")
tool_app.add_typer(task_app, name="task")
tool_app.add_typer(theorem_app, name="theorem")
tool_app.add_typer(ui_app, name="ui")
tool_app.add_typer(verify_app, name="verify")
tool_app.add_typer(goal_app, name="goal")
app.add_typer(tool_app, name="tool")


if __name__ == "__main__":
    app()

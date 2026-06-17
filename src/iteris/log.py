"""Small Rich-backed console helpers for Iteris."""

from __future__ import annotations

from contextlib import contextmanager

from rich.console import Console
from rich.live import Live
from rich.panel import Panel
from rich.rule import Rule
from rich.spinner import Spinner
from rich.table import Table
from rich.text import Text

console = Console()
PREFIX = "[bold cyan]\\[ITERIS][/bold cyan]"


def banner(version: str) -> None:
    title = Text.assemble(("Iteris", "bold cyan"), (f" v{version}", "dim"))
    console.print()
    console.print(Rule(title=title, style="cyan", align="center"))


def info(message: str) -> None:
    console.print(f"{PREFIX} {message}")


def step(message: str) -> None:
    console.print(f"{PREFIX} [dim]>[/dim] {message}")


def success(message: str) -> None:
    console.print(f"{PREFIX} [green]OK[/green] {message}")


def warn(message: str) -> None:
    console.print(f"{PREFIX} [yellow]WARN[/yellow] {message}")


def error(message: str) -> None:
    console.print(f"{PREFIX} [bold red]ERROR[/bold red] {message}")


def header(title: str) -> None:
    console.print()
    console.print(Rule(title=f"[bold]{title}[/bold]", style="cyan", align="left"))


def key_value(rows: dict[str, str]) -> None:
    table = Table(show_header=False, border_style="dim", padding=(0, 1))
    table.add_column("Key", style="bold")
    table.add_column("Value")
    for key, value in rows.items():
        table.add_row(key, value)
    console.print(table)


def results_table(rows: list[tuple[str, str, str]], title: str = "") -> None:
    table = Table(title=title or None, border_style="dim", padding=(0, 1))
    table.add_column("Name", style="bold")
    table.add_column("Status")
    table.add_column("Detail")
    styles = {
        "ok": "green",
        "warning": "yellow",
        "error": "red",
        "missing": "red",
        "skipped": "dim",
    }
    for name, status, detail in rows:
        style = styles.get(status.lower(), "")
        rendered = f"[{style}]{status}[/{style}]" if style else status
        table.add_row(name, rendered, detail)
    console.print(table)


def panel(content: str, title: str = "Next steps") -> None:
    console.print(Panel(content, title=title, border_style="dim", padding=(1, 2)))


@contextmanager
def thinking(message: str = "Thinking…"):
    """Show a spinner while a long-running monitor/LLM call is in progress."""
    with Live(Spinner("dots", text=message), console=console, refresh_per_second=10):
        yield

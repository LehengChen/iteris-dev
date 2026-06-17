"""Message commands: structured supervisor->worker steering."""

from __future__ import annotations

import json
import subprocess
import time
from pathlib import Path
from typing import Any

import typer

from iteris import log
from iteris.messages import MessageError, ack as ack_message, list_messages, send as send_message
from iteris.project import require_project
from iteris.tmux import capture_pane, tmux_session_alive

app = typer.Typer(help="Send, list, and acknowledge structured project messages.")

# An interactive agent CLI treats a text+Enter key flood as a paste and swallows
# the trailing Enter, leaving the notice unsubmitted in the composer. Send the
# text first, pause, then send Enter as a SEPARATE keystroke so paste-handling
# cannot eat it.
_ENTER_DELAY_SECONDS = 0.6


# Leader glyphs an interactive agent TUI prints in front of its live input box.
# The composer is always rendered at the very BOTTOM of the pane, so the
# bottom-most line bearing one of these is the live input line; anything above
# is scrollback (history, `message list`/`ack` echoes), and anything below is
# the status footer.
_COMPOSER_MARKERS = ("❯", "›", "»", ">")


def _composer_region(pane: str) -> str | None:
    """The live composer region (input line + status footer), or None.

    Returns the text from the bottom-most prompt-marker line to the end of the
    pane. ``None`` means no input box could be located (rendering quirk /
    capture too small) — the caller treats that as "cannot prove still held"
    rather than retrying blindly.
    """
    rows = pane.splitlines()
    for i in range(len(rows) - 1, -1, -1):
        stripped = rows[i].lstrip()
        if any(stripped.startswith(m) for m in _COMPOSER_MARKERS):
            return "\n".join(rows[i:])
    return None


def _composer_holds(session_name: str, msg_id: str) -> bool:
    """True only if ``msg_id`` sits in the LIVE composer input line.

    The old check tested ``msg_id in <40-line pane tail>``, which false-fired
    whenever a *successfully submitted* notice was still in scrollback or the
    agent's own `message list`/`ack` echoed the id — reporting a working
    delivery as `delivered:false` and firing a spurious extra Enter. Scope the
    test to the composer region (bottom-most input line onward) so only an
    unsubmitted notice still in the input box counts as held.

    Best-effort: if the pane cannot be captured, or no input box can be located,
    return False (do not claim "still held" → no spurious retry-Enter); the
    text+Enter was already sent and the most likely state is submitted.
    """
    try:
        pane = capture_pane(session_name, lines=40)
    except Exception:  # noqa: BLE001 — capture is advisory
        return False
    region = _composer_region(pane)
    if region is None:
        return False
    return msg_id in region


def _verify_submitted(session_name: str, msg_id: str) -> bool:
    """Confirm the notice left the composer, retrying Enter once if it lingers.

    A submitted notice scrolls into the pane history but is no longer the live
    input line; we approximate "submitted" as "the msg_id no longer appears in
    the recent pane tail". If it still appears, the Enter was likely swallowed,
    so press Enter once more and re-check.
    """
    if not _composer_holds(session_name, msg_id):
        return True
    subprocess.run(
        ["tmux", "send-keys", "-t", session_name, "Enter"],
        check=True,
        capture_output=True,
        text=True,
        timeout=10,
    )
    time.sleep(_ENTER_DELAY_SECONDS)
    return not _composer_holds(session_name, msg_id)


def notify_run_session(recipient: Path, message: dict[str, Any]) -> dict[str, Any]:
    """Queue a one-line notice into the recipient's live run pane and submit it.

    The inbox alone is not a timely channel: supervision measured 50+ minutes
    unread via inbox-only delivery vs under a minute after a tmux pane line.

    The notice text is sent literally with ``send-keys -l``, then after a short
    delay Enter is sent as a SEPARATE keystroke. Interactive agent CLIs treat a
    combined text+Enter key flood as a paste and swallow the trailing Enter, so
    the two must not ride in one call. Delivery is then verified against the
    pane (retrying Enter once); ``delivered`` reflects that verification.

    Best-effort: any failure leaves the message in the inbox and never raises
    out of the send command.
    """
    session_name = ""
    try:
        from iteris.commands.workflow import default_session_name

        session_name = default_session_name(recipient)
        if not tmux_session_alive(session_name):
            return {"delivered": False, "session_name": session_name, "reason": "no live run session"}
        line = (
            f"[iteris message] Unread {message['priority']}-priority {message['type']} {message['msg_id']} in the project inbox: "
            "run `iteris tool message list . --unread --json`, act on it, then ack it with "
            f"`iteris tool message ack . --msg-id {message['msg_id']} --disposition applied|noted|declined`."
        )
        # 1) Type the notice literally (-l), so its content is never read as keys.
        subprocess.run(
            ["tmux", "send-keys", "-t", session_name, "-l", line],
            check=True,
            capture_output=True,
            text=True,
            timeout=10,
        )
        # 2) Pause so the composer settles, then submit with a SEPARATE Enter.
        time.sleep(_ENTER_DELAY_SECONDS)
        subprocess.run(
            ["tmux", "send-keys", "-t", session_name, "Enter"],
            check=True,
            capture_output=True,
            text=True,
            timeout=10,
        )
        delivered = _verify_submitted(session_name, str(message["msg_id"]))
    except Exception as exc:  # noqa: BLE001 — the message is already in the inbox; delivery must not fail the send
        return {"delivered": False, "session_name": session_name, "reason": str(exc)}
    if not delivered:
        return {"delivered": False, "session_name": session_name, "reason": "notice still sits in the input box after retry"}
    return {"delivered": True, "session_name": session_name}


@app.command("send")
def send(
    project_path: str = typer.Argument(".", help="Sender's Iteris project path."),
    to: str = typer.Option(..., "--to", help="Recipient Iteris project path."),
    body: str = typer.Option(..., "--body", help="Message body."),
    type: str = typer.Option("nudge", "--type", help="Message type: nudge|hint|question."),
    priority: str = typer.Option("normal", "--priority", help="normal|high."),
    sender: str = typer.Option("supervisor", "--sender", help="supervisor|human."),
    ref: list[str] = typer.Option([], "--ref", help="Related ids (fact:..., dir-...). Repeatable."),
    notify: bool = typer.Option(True, "--notify/--no-notify", help="For high-priority messages, also queue a notice line into the recipient's live run pane."),
    json_output: bool = typer.Option(False, "--json", help="Print machine-readable JSON."),
) -> None:
    """Append a message to the recipient project's inbox."""
    require_project(project_path)  # sender context must be a project
    recipient = require_project(to)
    try:
        message = send_message(
            recipient, body=body, type=type, priority=priority, sender=sender, refs=list(ref)
        )
    except MessageError as exc:
        raise typer.BadParameter(str(exc)) from exc
    delivery = notify_run_session(recipient, message) if (notify and priority == "high") else None
    if json_output:
        typer.echo(json.dumps({**message, "delivery": delivery}, indent=2, ensure_ascii=False))
        return
    log.success(f"sent {message['msg_id']} -> {recipient.name} ({type}, {priority})")
    if delivery is not None:
        if delivery["delivered"]:
            log.info(f"notice queued into live session {delivery['session_name']}")
        else:
            log.warn(f"pane notice not delivered ({delivery['reason']}); message waits in the inbox")


@app.command("list")
def list_cmd(
    project_path: str = typer.Argument(".", help="Iteris project path."),
    unread: bool = typer.Option(False, "--unread", help="Only messages without an ack."),
    json_output: bool = typer.Option(False, "--json", help="Print machine-readable JSON."),
) -> None:
    """List this project's messages (merged inbox + ack view, oldest first)."""
    root = require_project(project_path)
    rows = list_messages(root, unread_only=unread)
    if json_output:
        typer.echo(json.dumps({"count": len(rows), "messages": rows}, indent=2, ensure_ascii=False))
        return
    if not rows:
        log.info("no messages" + (" (unread)" if unread else ""))
        return
    table = []
    for row in rows:
        state = row["ack"]["disposition"] if row.get("acked") else "UNREAD"
        kind = row.get("type", "")
        if row.get("priority") == "high":
            kind += " high"
        table.append((row["msg_id"], state, f"[{kind}] {row.get('body', '')[:60]}"))
    log.results_table(table, title="Messages")


@app.command("ack")
def ack_cmd(
    project_path: str = typer.Argument(".", help="Iteris project path."),
    msg_id: str = typer.Option(..., "--msg-id", help="Message id to acknowledge."),
    disposition: str = typer.Option(..., "--disposition", help="applied|noted|declined."),
    note: str = typer.Option("", "--note", help="Optional short note on what was done."),
    json_output: bool = typer.Option(False, "--json", help="Print machine-readable JSON."),
) -> None:
    """Record the worker's receipt and decision for one message."""
    root = require_project(project_path)
    try:
        entry = ack_message(root, msg_id=msg_id, disposition=disposition, note=note)
    except MessageError as exc:
        raise typer.BadParameter(str(exc)) from exc
    if json_output:
        typer.echo(json.dumps(entry, indent=2, ensure_ascii=False))
        return
    log.success(f"acked {msg_id} ({disposition})")

"""Codex exec JSONL logging utilities."""

from __future__ import annotations

import hashlib
import json
import os
import shlex
import signal
import subprocess
import sys
from pathlib import Path
from typing import Any, Callable

from iteris.project import append_jsonl, now_iso, write_json

CODEX_EVENTS_FILENAME = "codex.events.jsonl"
CODEX_LOG_FILENAME = "codex.log"
CODEX_STDERR_FILENAME = "codex.stderr.log"
CODEX_LOG_MANIFEST_FILENAME = "log_manifest.json"
CODEX_RUN_INDEX = ".iteris/logs/CODEX_RUN_INDEX.jsonl"
MAX_RENDERED_VALUE_CHARS = 12000


def ensure_codex_exec_json(command: list[str]) -> list[str]:
    """Return a Codex exec command that emits JSONL events on stdout."""
    if len(command) < 2 or command[1] != "exec" or "--json" in command:
        return command
    return [command[0], "exec", "--json", *command[2:]]


def build_child_env(env_updates: dict[str, str]) -> dict[str, str]:
    """Child-process environment with the `iteris` CLI guaranteed on PATH.

    Subagents and verification agents call `iteris tool ...` for gating, fact
    submission, and verification; when the venv's scripts dir is absent from
    their PATH they degrade nondeterministically (skip gating, or write
    "proposed verification" prose instead of submitting). Pin the dir holding
    the running interpreter's console scripts rather than trusting the
    inherited environment.
    """
    env = os.environ.copy()
    env.update(env_updates)
    if not sys.executable:
        # Embedded interpreters may report an empty executable; Path("").parent
        # is ".", and the CWD must never be pinned onto a child's PATH.
        return env
    scripts_dir = str(Path(sys.executable).parent)
    path_value = env.get("PATH", "")
    if scripts_dir not in path_value.split(os.pathsep):
        env["PATH"] = scripts_dir + os.pathsep + path_value if path_value else scripts_dir
    return env


def run_codex_exec_json(
    *,
    project_root: Path,
    run_dir: Path,
    process_kind: str,
    run_id: str,
    command: list[str],
    prompt: str,
    prompt_path: Path,
    env_updates: dict[str, str],
    timeout_seconds: int,
    on_spawn: Callable[[int, int], None] | None = None,
    executor: str = "codex",
    render_fn: Callable[..., dict[str, Any]] | None = None,
    log_adapter: str | None = None,
) -> dict[str, Any]:
    """Run a headless agent CLI, preserving raw JSONL events and a text log.

    Despite the legacy name, this is executor-agnostic: ``executor`` selects the
    backend tag and ``render_fn`` selects how the captured JSONL stream is
    rendered into the human-readable text log (codex events vs claude
    stream-json). The spawn/timeout/process-group/manifest machinery is shared.
    The events filename stays ``codex.events.jsonl`` for both executors; the
    manifest's ``executor``/``log_adapter`` fields make each run self-describing.

    ``on_spawn`` is called with ``(pid, pgid)`` right after the child starts, so
    callers can persist the process group while it runs. The child is launched in
    its own session (its pgid == pid), separate from the worker, so a drainer
    must know this pgid to reap the CLI/node subtree if the worker is killed.
    """
    root = project_root.resolve()
    run_dir = run_dir.resolve()
    # No-op for non-codex commands (claude's argv[1] is "-p", not "exec").
    cmd = ensure_codex_exec_json([str(item) for item in command])
    _render_events = render_fn or render_codex_events
    events_path = run_dir / CODEX_EVENTS_FILENAME
    text_log_path = run_dir / CODEX_LOG_FILENAME
    stderr_path = run_dir / CODEX_STDERR_FILENAME
    manifest_path = run_dir / CODEX_LOG_MANIFEST_FILENAME
    started_at = now_iso()
    timed_out = False
    returncode: int | None = None
    error: str | None = None

    env = build_child_env(env_updates)
    run_dir.mkdir(parents=True, exist_ok=True)
    header_lines = [
        f"started_at: {started_at}",
        f"command: {shlex.join(cmd)}",
        f"prompt_file: {_rel(root, prompt_path)}",
        f"events_log: {_rel(root, events_path)}",
        f"stderr_log: {_rel(root, stderr_path)}",
        "",
    ]
    text_log_path.write_text(
        "\n".join(header_lines)
        + f"--- {executor} exec is running; raw JSONL events stream to codex.events.jsonl, stderr to codex.stderr.log ---\n",
        encoding="utf-8",
    )

    with events_path.open("w", encoding="utf-8") as events_handle, stderr_path.open("w", encoding="utf-8") as stderr_handle:
        proc = subprocess.Popen(
            cmd,
            cwd=root,
            env=env,
            stdin=subprocess.PIPE,
            stdout=events_handle,
            stderr=stderr_handle,
            text=True,
            start_new_session=True,
        )
        if on_spawn is not None:
            try:
                pgid = os.getpgid(proc.pid)
            except (ProcessLookupError, OSError):
                pgid = proc.pid
            try:
                on_spawn(proc.pid, pgid)
            except Exception:
                pass
        try:
            proc.communicate(input=prompt, timeout=timeout_seconds)
            returncode = proc.returncode
        except subprocess.TimeoutExpired:
            timed_out = True
            error = f"codex exec timed out after {timeout_seconds} seconds"
            _terminate_process_group(proc)
            try:
                proc.communicate(timeout=1)
            except Exception:
                pass
            returncode = None

    render = _render_events(
        events_path,
        text_log_path,
        header_lines=header_lines,
    )
    _append_stderr_to_text_log(stderr_path, text_log_path)
    completed_at = now_iso()
    manifest = {
        "schema_version": "iteris.codex_exec_log.v0",
        "executor": executor,
        "log_adapter": log_adapter or executor,
        "process_kind": process_kind,
        "run_id": run_id,
        "project_path": str(root),
        "run_dir": _rel(root, run_dir),
        "command": cmd,
        "prompt_path": _rel(root, prompt_path),
        "events_path": _rel(root, events_path),
        "stderr_path": _rel(root, stderr_path),
        "text_log_path": _rel(root, text_log_path),
        "manifest_path": _rel(root, manifest_path),
        "started_at": started_at,
        "completed_at": completed_at,
        "returncode": returncode,
        "timed_out": timed_out,
        "error": error,
        "session_id": render.get("session_id"),
        "event_count": render["event_count"],
        "malformed_line_count": render["malformed_line_count"],
        "events_size": events_path.stat().st_size if events_path.exists() else 0,
        "events_sha256": _sha256(events_path) if events_path.exists() else None,
        "stderr_size": stderr_path.stat().st_size if stderr_path.exists() else 0,
        "stderr_sha256": _sha256(stderr_path) if stderr_path.exists() else None,
        "text_log_size": text_log_path.stat().st_size if text_log_path.exists() else 0,
        "text_log_sha256": _sha256(text_log_path) if text_log_path.exists() else None,
    }
    write_json(manifest_path, manifest)
    append_jsonl(root / CODEX_RUN_INDEX, manifest)
    return manifest


def _append_stderr_to_text_log(stderr_path: Path, text_log_path: Path) -> None:
    if not stderr_path.exists() or stderr_path.stat().st_size == 0:
        return
    stderr_text = stderr_path.read_text(encoding="utf-8", errors="replace")
    with text_log_path.open("a", encoding="utf-8") as out:
        out.write("\n--- codex stderr ---\n")
        out.write(_truncate(stderr_text))
        if not stderr_text.endswith("\n"):
            out.write("\n")


def render_codex_events(events_path: Path, text_log_path: Path, *, header_lines: list[str] | None = None) -> dict[str, Any]:
    """Render Codex JSONL events into a compact human-readable log."""
    event_count = 0
    malformed_line_count = 0
    session_id: str | None = None
    text_log_path.parent.mkdir(parents=True, exist_ok=True)
    with text_log_path.open("w", encoding="utf-8") as out:
        for line in header_lines or []:
            out.write(line.rstrip() + "\n")
        out.write("--- rendered codex exec events ---\n")
        if not events_path.exists():
            out.write("[missing events file]\n")
            return {"event_count": 0, "malformed_line_count": 0, "session_id": None}
        with events_path.open("r", encoding="utf-8", errors="replace") as handle:
            for line_no, raw in enumerate(handle, start=1):
                raw = raw.rstrip("\n")
                if not raw.strip():
                    continue
                event_count += 1
                try:
                    event = json.loads(raw)
                except json.JSONDecodeError:
                    malformed_line_count += 1
                    out.write(f"\n[{line_no}] raw: {_truncate(raw)}\n")
                    continue
                if event.get("type") == "thread.started" and event.get("thread_id"):
                    session_id = str(event.get("thread_id"))
                if event.get("type") == "session_meta" and isinstance(event.get("payload"), dict):
                    session_id = str(event["payload"].get("id") or "") or session_id
                rendered = _render_event(event, line_no=line_no)
                if rendered:
                    out.write(rendered)
                    if not rendered.endswith("\n"):
                        out.write("\n")
    return {"event_count": event_count, "malformed_line_count": malformed_line_count, "session_id": session_id}


def _render_event(event: dict[str, Any], *, line_no: int) -> str:
    if "item" in event or str(event.get("type") or "").startswith(("thread.", "turn.")):
        return _render_current_event(event, line_no=line_no)

    timestamp = str(event.get("timestamp") or "?")
    event_type = str(event.get("type") or "?")
    payload = event.get("payload")
    if not isinstance(payload, dict):
        return f"\n[{timestamp}] {event_type}: {_truncate(json.dumps(payload, ensure_ascii=False))}\n"

    payload_type = str(payload.get("type") or "")
    prefix = f"\n[{timestamp}] {event_type}{':' + payload_type if payload_type else ''}"
    if event_type == "session_meta":
        return (
            f"{prefix}\n"
            f"  session_id: {payload.get('id')}\n"
            f"  cwd: {payload.get('cwd')}\n"
            f"  cli_version: {payload.get('cli_version')}\n"
            f"  source: {payload.get('source')}\n"
        )
    if payload_type in {"task_started", "task_complete", "patch_apply_end"}:
        return f"{prefix}\n{_render_mapping(payload, skip={'type'})}"
    if payload_type in {"agent_message", "user_message"}:
        message = str(payload.get("message") or "")
        return f"{prefix}\n{_indent(_truncate(message))}\n"
    if payload_type == "token_count":
        info = payload.get("info") if isinstance(payload.get("info"), dict) else {}
        usage = info.get("total_token_usage") if isinstance(info.get("total_token_usage"), dict) else {}
        return f"{prefix}\n{_indent(json.dumps(usage, ensure_ascii=False, separators=(',', ':')))}\n"
    if payload_type == "message":
        role = str(payload.get("role") or "?")
        text = _message_text(payload)
        if role in {"developer", "system", "user"}:
            return f"{prefix}\n  role: {role}\n  content_chars: {len(text)}\n"
        return f"{prefix}\n  role: {role}\n{_indent(_truncate(text))}\n"
    if payload_type in {"function_call", "custom_tool_call"}:
        name = payload.get("name")
        arguments = payload.get("arguments", payload.get("input", ""))
        return f"{prefix}\n  name: {name}\n  call_id: {payload.get('call_id')}\n{_indent(_truncate(str(arguments)))}\n"
    if payload_type in {"function_call_output", "custom_tool_call_output"}:
        output = str(payload.get("output") or "")
        return f"{prefix}\n  call_id: {payload.get('call_id')}\n{_indent(_truncate(output))}\n"
    if payload_type == "reasoning":
        summary = payload.get("summary") if isinstance(payload.get("summary"), list) else []
        summary_text = _truncate(json.dumps(summary, ensure_ascii=False)) if summary else "encrypted reasoning omitted; raw event retained"
        return f"{prefix}\n{_indent(summary_text)}\n"
    return f"{prefix}\n{_render_mapping(payload, skip=set())}"


def _render_current_event(event: dict[str, Any], *, line_no: int) -> str:
    event_type = str(event.get("type") or "?")
    item = event.get("item") if isinstance(event.get("item"), dict) else None
    if event_type == "thread.started":
        return f"\n[{line_no}] thread.started\n  thread_id: {event.get('thread_id')}\n"
    if event_type == "turn.started":
        return f"\n[{line_no}] turn.started\n"
    if event_type == "turn.completed":
        usage = event.get("usage") if isinstance(event.get("usage"), dict) else {}
        return f"\n[{line_no}] turn.completed\n{_indent(json.dumps(usage, ensure_ascii=False, separators=(',', ':')))}\n"
    if item is None:
        return f"\n[{line_no}] {event_type}\n{_render_mapping(event, skip={'type'})}"

    item_type = str(item.get("type") or "?")
    prefix = f"\n[{line_no}] {event_type}:{item_type}"
    if item_type == "agent_message":
        return f"{prefix}\n{_indent(_truncate(str(item.get('text') or '')))}\n"
    if item_type == "command_execution":
        parts = [
            f"{prefix}",
            f"  command: {_truncate(str(item.get('command') or ''))}",
            f"  status: {item.get('status')}",
            f"  exit_code: {item.get('exit_code')}",
        ]
        output = str(item.get("aggregated_output") or "")
        if output:
            parts.append(_indent(_truncate(output)))
        return "\n".join(parts) + "\n"
    if item_type == "file_change":
        changes = item.get("changes") if isinstance(item.get("changes"), list) else []
        rendered_changes = json.dumps(changes, ensure_ascii=False, separators=(",", ":"))
        return f"{prefix}\n  status: {item.get('status')}\n{_indent(_truncate(rendered_changes))}\n"
    if item_type == "web_search":
        return f"{prefix}\n  query: {_truncate(str(item.get('query') or ''))}\n  action: {_truncate(json.dumps(item.get('action'), ensure_ascii=False, separators=(',', ':')))}\n"
    return f"{prefix}\n{_render_mapping(item, skip={'type'})}"


def _render_mapping(payload: dict[str, Any], *, skip: set[str]) -> str:
    rows = []
    for key, value in payload.items():
        if key in skip:
            continue
        if isinstance(value, (dict, list)):
            rendered = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
        else:
            rendered = str(value)
        rows.append(f"  {key}: {_truncate(rendered)}")
    return "\n".join(rows) + ("\n" if rows else "")


def _message_text(payload: dict[str, Any]) -> str:
    content = payload.get("content")
    if not isinstance(content, list):
        return str(content or "")
    parts: list[str] = []
    for item in content:
        if isinstance(item, dict):
            parts.append(str(item.get("text") or item.get("input") or item.get("output") or ""))
        else:
            parts.append(str(item))
    return "\n".join(part for part in parts if part)


def _terminate_process_group(proc: subprocess.Popen[str]) -> None:
    if proc.poll() is not None:
        return
    if os.name == "posix":
        try:
            os.killpg(proc.pid, signal.SIGTERM)
            proc.wait(timeout=5)
            return
        except Exception:
            pass
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except Exception:
            proc.kill()
    else:  # pragma: no cover - Iteris targets macOS/Linux, but keep fallback sane.
        proc.kill()
    try:
        proc.wait(timeout=5)
    except Exception:
        pass


def _indent(text: str) -> str:
    return "\n".join(f"  {line}" for line in text.splitlines()) if text else "  "


def _truncate(text: str, limit: int = MAX_RENDERED_VALUE_CHARS) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n...[truncated {len(text) - limit} chars; see raw codex.events.jsonl]"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _rel(root: Path, path: Path) -> str:
    try:
        return str(path.resolve().relative_to(root))
    except ValueError:
        return str(path.resolve())


# --- Structured normalization (for the dashboard UI) -----------------------
#
# The text renderers above are for human reading. The dashboard needs the same
# events mapped into a compact, stable schema the React client can render
# directly (mirrors the Archon LogEntry shape):
#   {ts, event, content?, tool?, input?, level?, raw_type?, ...}
# The mapping knowledge is shared with `_render_event`/`_render_current_event`;
# this just emits dicts instead of text. The normalizer is stateful so that
# session ids and turn usage carry across lines.

NORMALIZE_RENDER_LIMIT = 4000


class CodexEventNormalizer:
    """Map raw `codex exec --json` events into unified LogEntry dicts.

    Stateful across a stream (tracks session id). `feed()` returns a single
    LogEntry dict, or None for events that carry no display value (e.g.
    thread.started, turn.started, session_meta).
    """

    def __init__(self, *, clock: Any = None) -> None:
        self._clock = clock or now_iso
        self.session_id: str | None = None

    def _ts(self, event: dict[str, Any]) -> str:
        ts = event.get("timestamp")
        return str(ts) if ts else self._clock()

    def feed(self, event: dict[str, Any]) -> dict[str, Any] | None:
        if not isinstance(event, dict):
            return None
        if "item" in event or str(event.get("type") or "").startswith(("thread.", "turn.")):
            return self._feed_current(event)
        return self._feed_session(event)

    # Format A: thread/turn/item events
    def _feed_current(self, event: dict[str, Any]) -> dict[str, Any] | None:
        event_type = str(event.get("type") or "")
        ts = self._ts(event)
        if event_type == "thread.started":
            self.session_id = event.get("thread_id") or self.session_id
            return None
        if event_type == "turn.started":
            return None
        if event_type == "turn.completed":
            usage = event.get("usage") if isinstance(event.get("usage"), dict) else {}
            return {
                "ts": ts,
                "event": "session_end",
                "raw_type": "turn.completed",
                "input_tokens": usage.get("input_tokens"),
                "output_tokens": usage.get("output_tokens"),
                "summary": json.dumps(usage, ensure_ascii=False),
            }
        item = event.get("item") if isinstance(event.get("item"), dict) else None
        if item is None:
            return {"ts": ts, "event": "text", "raw_type": event_type,
                    "content": _truncate(json.dumps(event, ensure_ascii=False), NORMALIZE_RENDER_LIMIT)}
        item_type = str(item.get("type") or "")
        if item_type == "agent_message":
            return {"ts": ts, "event": "text", "raw_type": item_type,
                    "content": str(item.get("text") or "")}
        if item_type == "command_execution":
            command = str(item.get("command") or "")
            status = str(item.get("status") or "")
            if status == "in_progress":
                return {"ts": ts, "event": "tool_call", "raw_type": item_type,
                        "tool": _infer_tool(command), "input": {"command": command}}
            return {
                "ts": ts,
                "event": "tool_result",
                "raw_type": item_type,
                "tool": _infer_tool(command),
                "level": "error" if item.get("exit_code") not in (0, None) else "info",
                "content": _truncate(str(item.get("aggregated_output") or ""), NORMALIZE_RENDER_LIMIT),
            }
        if item_type == "file_change":
            changes = item.get("changes") if isinstance(item.get("changes"), list) else []
            return {"ts": ts, "event": "tool_call", "raw_type": item_type, "tool": "Edit",
                    "input": {"changes": changes}}
        if item_type == "web_search":
            return {"ts": ts, "event": "tool_call", "raw_type": item_type, "tool": "WebSearch",
                    "input": {"query": item.get("query")}}
        return {"ts": ts, "event": "text", "raw_type": item_type,
                "content": _truncate(json.dumps(item, ensure_ascii=False), NORMALIZE_RENDER_LIMIT)}

    # Format B: timestamped {type, payload} events
    def _feed_session(self, event: dict[str, Any]) -> dict[str, Any] | None:
        ts = self._ts(event)
        payload = event.get("payload")
        if not isinstance(payload, dict):
            return None
        ptype = str(payload.get("type") or "")
        if ptype == "session_meta":
            self.session_id = payload.get("id") or self.session_id
            return None
        if ptype in {"agent_message", "user_message"}:
            return {"ts": ts, "event": "text", "raw_type": ptype,
                    "content": str(payload.get("message") or "")}
        if ptype == "message":
            # Interactive rollouts replay the full conversation as `message`
            # items: the `developer` permissions block, the `user`
            # environment/prompt echo, and `assistant` turns that exactly
            # duplicate the `agent_message` events. All are noise or redundant
            # here, so drop them and let `agent_message` / `user_message`
            # carry the real content.
            return None
        if ptype == "reasoning":
            summary = payload.get("summary") if isinstance(payload.get("summary"), list) else []
            text = json.dumps(summary, ensure_ascii=False) if summary else "(encrypted reasoning)"
            return {"ts": ts, "event": "thinking", "raw_type": ptype, "content": _truncate(text, NORMALIZE_RENDER_LIMIT)}
        if ptype in {"function_call", "custom_tool_call"}:
            return {"ts": ts, "event": "tool_call", "raw_type": ptype,
                    "tool": str(payload.get("name") or "tool"),
                    "input": {"arguments": payload.get("arguments", payload.get("input"))}}
        if ptype in {"function_call_output", "custom_tool_call_output"}:
            return {"ts": ts, "event": "tool_result", "raw_type": ptype,
                    "content": _truncate(str(payload.get("output") or ""), NORMALIZE_RENDER_LIMIT)}
        if ptype in {"task_started", "task_complete", "patch_apply_end"}:
            return {"ts": ts, "event": "text", "raw_type": ptype,
                    "content": _truncate(json.dumps(payload, ensure_ascii=False), NORMALIZE_RENDER_LIMIT)}
        # token_count and other low-signal events are dropped from the UI.
        return None


def _infer_tool(command: str) -> str:
    stripped = command.strip()
    if stripped.startswith(("/bin/bash", "bash", "sh ", "/bin/sh")):
        return "Bash"
    head = stripped.split(None, 1)[0] if stripped else ""
    return head or "shell"


def normalize_codex_file(path: Path, *, max_bytes: int | None = None) -> list[dict[str, Any]]:
    """Normalize a codex JSONL file into LogEntry dicts.

    With ``max_bytes``, only complete lines fully contained in the first
    ``max_bytes`` bytes are processed. This lets a live tail use the same byte
    offset as its baseline snapshot, so streamed appends never overlap it.
    """
    normalizer = CodexEventNormalizer()
    entries: list[dict[str, Any]] = []
    if not path.exists():
        return entries

    def _feed(line: str) -> None:
        line = line.strip()
        if not line:
            return
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            return
        mapped = normalizer.feed(event)
        if mapped is not None:
            entries.append(mapped)

    # Split records on '\n' ONLY — never str.splitlines(): JSON strings may
    # legally contain raw U+2028/U+2029, which splitlines() would break on,
    # silently dropping the event.
    if max_bytes is None:
        with path.open("r", encoding="utf-8", errors="replace", newline="\n") as handle:
            for line in handle:  # streamed, not slurped — rollouts get large
                _feed(line)
        return entries

    with path.open("rb") as handle:
        data = handle.read(max_bytes)
    if len(data) == max_bytes and not data.endswith(b"\n"):
        # Drop the trailing partial line; the tail will stream it once complete.
        head, _, _ = data.rpartition(b"\n")
        data = head
    for line in data.decode("utf-8", errors="replace").split("\n"):
        _feed(line)
    return entries

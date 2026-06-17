"""`iteris dashboard` — launch the web UI.

Spawns a Node Fastify server (from the packaged `ui/` tree) that serves the
built React SPA and proxies data from `iteris tool ui ... --json`. Modeled on
the Archon dashboard launcher: npm install / vite build when stale, spawn the
server, probe it on a port (advancing on conflict), then block until Ctrl-C.
"""

from __future__ import annotations

import atexit
import errno
import hashlib
import json
import os
import shutil
import signal
import socket
import subprocess
import time
import urllib.request
import webbrowser
from importlib import resources
from pathlib import Path

import typer

from iteris import log
from iteris.project import require_project

_MAX_PORT_ATTEMPTS = 8
_MIN_NODE_MAJOR = 20  # fastify 5 (ui/server) requires Node >= 20.
_PORT_IN_USE_EXIT = 98  # server's exit code for EADDRINUSE (see ui/server/src/index.ts)


def _ui_dir() -> Path:
    return Path(str(resources.files("iteris").joinpath("ui")))


def _node_major(node_exe: str) -> int | None:
    """Return the major version of a node binary, or None if it can't be read."""
    try:
        out = subprocess.run([node_exe, "--version"], capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.SubprocessError):
        return None
    raw = (out.stdout or "").strip().lstrip("v")
    try:
        return int(raw.split(".", 1)[0])
    except (ValueError, IndexError):
        return None


def _resolve_node_bin_dir() -> Path | None:
    """Find a directory holding Node >= 20, to prepend to PATH for subprocesses.

    The launcher is spawned from the iteris venv, so PATH may carry a too-old
    distro/nvm node (Ubuntu's apt nodejs is v12; this box defaulted to v16).
    Prefer the system node when it's new enough; otherwise scan nvm's installed
    versions for the newest >= 20 so `iteris dashboard` just works.
    """
    system = shutil.which("node")
    if system and (_node_major(system) or 0) >= _MIN_NODE_MAJOR:
        return None  # PATH node is fine — no override need
    nvm_dir = os.environ.get("NVM_DIR") or "/usr/local/nvm"
    versions = Path(nvm_dir) / "versions" / "node"
    best: tuple[int, Path] | None = None
    if versions.is_dir():
        for entry in versions.iterdir():
            node_exe = entry / "bin" / "node"
            if not node_exe.exists():
                continue
            major = _node_major(str(node_exe)) or 0
            if major >= _MIN_NODE_MAJOR and (best is None or major > best[0]):
                best = (major, node_exe.parent)
    return best[1] if best else None


def _node_env() -> dict[str, str]:
    """Environment for node/npm/npx subprocesses, with a >=20 node on PATH."""
    env = dict(os.environ)
    bin_dir = _resolve_node_bin_dir()
    if bin_dir is not None:
        env["PATH"] = f"{bin_dir}{os.pathsep}{env.get('PATH', '')}"
    return env


def _check_node(env: dict[str, str]) -> None:
    if shutil.which("npm", path=env.get("PATH")) is None:
        log.error("node and npm are required for the dashboard (Node >= 20).")
        raise typer.Exit(1)
    node_exe = shutil.which("node", path=env.get("PATH"))
    major = _node_major(node_exe) if node_exe else None
    if node_exe is None or major is None:
        log.error("node and npm are required for the dashboard (Node >= 20).")
        raise typer.Exit(1)
    if major < _MIN_NODE_MAJOR:
        log.error(
            f"Node {major} found, but the dashboard needs Node >= {_MIN_NODE_MAJOR}. "
            f"Install it (e.g. `nvm install {_MIN_NODE_MAJOR}`) and re-run."
        )
        raise typer.Exit(1)


def _install_if_needed(pkg_dir: Path, label: str, env: dict[str, str]) -> None:
    node_modules = pkg_dir / "node_modules"
    # Dedicated stamp file — node_modules/.package-lock.json belongs to npm.
    stamp = node_modules / ".iteris-install-stamp"
    manifests = [pkg_dir / "package.json", pkg_dir / "package-lock.json"]
    fingerprint = _manifest_fingerprint(manifests)
    if node_modules.exists() and _read_install_fingerprint(stamp) == fingerprint:
        return
    log.step(f"Installing {label} dependencies…")
    command = ["npm", "ci"] if (pkg_dir / "package-lock.json").exists() else ["npm", "install"]
    subprocess.run(command, cwd=pkg_dir, check=True, env=env)
    try:
        stamp.write_text(json.dumps({"manifest_fingerprint": fingerprint, "installer": command[1]}))
    except OSError:
        pass


def _manifest_fingerprint(paths: list[Path]) -> str:
    digest = hashlib.sha256()
    for path in paths:
        digest.update(path.name.encode("utf-8"))
        digest.update(b"\0")
        if path.exists():
            digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def _read_install_fingerprint(stamp: Path) -> str | None:
    try:
        payload = json.loads(stamp.read_text())
    except (OSError, json.JSONDecodeError):
        return None
    value = payload.get("manifest_fingerprint")
    return value if isinstance(value, str) else None


def _needs_build(client_dir: Path) -> bool:
    index = client_dir / "dist" / "index.html"
    if not index.exists():
        return True
    newest = index.stat().st_mtime
    for path in (client_dir / "src").rglob("*"):
        if path.is_file() and path.stat().st_mtime > newest:
            return True
    for f in ("package.json", "vite.config.ts", "index.html"):
        p = client_dir / f
        if p.exists() and p.stat().st_mtime > newest:
            return True
    return False


def _build_client(client_dir: Path, env: dict[str, str]) -> None:
    log.step("Building client…")
    subprocess.run(["npm", "run", "build"], cwd=client_dir, check=True, env=env)


def _port_in_use(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            s.bind(("127.0.0.1", port))
            return False
        except OSError as exc:
            if exc.errno in (errno.EAFNOSUPPORT, errno.EADDRNOTAVAIL):
                return False  # no IPv4 loopback here; let the server's own bind decide
            return True


def _find_free_port(start: int) -> int | None:
    for port in range(start + 1, start + 12):
        if not _port_in_use(port):
            return port
    return None


def _wait_for_http(port: int, expected_project: str, timeout: float, proc: subprocess.Popen | None = None) -> bool:
    deadline = time.time() + timeout
    # `localhost` resolves to whichever loopback the server actually bound
    # (127.0.0.1, or ::1 on an IPv4-less host).
    url = f"http://localhost:{port}/api/project"
    while time.time() < deadline:
        if proc is not None and proc.poll() is not None:
            # Server died — no point waiting out the timeout; the caller
            # decides whether it was a port clash or a real launch failure.
            return False
        try:
            with urllib.request.urlopen(url, timeout=1.0) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                if data.get("projectPath") == expected_project:
                    return True
        except Exception:
            time.sleep(0.2)
    return False


def dashboard(
    project_path: str = typer.Argument(".", help="Iteris project path. Defaults to the current directory."),
    port: int = typer.Option(8099, "--port", "-p", help="Preferred port (advances if busy)."),
    dev: bool = typer.Option(False, "--dev", help="Dev mode: server with tsx watch + vite dev server."),
    open_browser: bool = typer.Option(True, "--open/--no-open", help="Open the dashboard in a browser."),
) -> None:
    """Launch the Iteris dashboard web UI."""
    root = require_project(project_path)
    ui_dir = _ui_dir()
    server_dir = ui_dir / "server"
    client_dir = ui_dir / "client"
    if not server_dir.exists() or not client_dir.exists():
        log.error("UI files not found in the package — installation may be incomplete.")
        raise typer.Exit(1)

    env = _node_env()
    _check_node(env)
    _install_if_needed(server_dir, "server", env)
    _install_if_needed(client_dir, "client", env)

    spawn_kwargs: dict = {"cwd": server_dir, "env": env}
    if os.name != "nt":
        spawn_kwargs["start_new_session"] = True

    def spawn(p: int) -> subprocess.Popen:
        return subprocess.Popen(
            ["node", "--import", "tsx", "src/index.ts", "--project", str(root), "--port", str(p)],
            **spawn_kwargs,
        )

    if dev:
        log.header("Iteris Dashboard (dev)")
        log.key_value({"Server": f"http://127.0.0.1:{port}", "Vite": "http://127.0.0.1:5173"})
        proc = spawn(port)
        atexit.register(lambda: _shutdown(proc))
        try:
            subprocess.run(["npm", "run", "dev"], cwd=client_dir, env=env)
        except KeyboardInterrupt:
            pass
        finally:
            _shutdown(proc)
        return

    if _needs_build(client_dir):
        _build_client(client_dir, env)
    else:
        log.success("Client up to date")

    final_port = port if not _port_in_use(port) else (_find_free_port(port) or port)
    proc = spawn(final_port)
    atexit.register(lambda: _shutdown(proc))

    for attempt in range(_MAX_PORT_ATTEMPTS):
        wait = 12.0 if attempt == 0 else 4.0
        if _wait_for_http(final_port, str(root), timeout=wait, proc=proc):
            break
        if proc.poll() is not None and proc.returncode != _PORT_IN_USE_EXIT:
            # The server exited on its own for a non-port reason — that's a
            # launch failure (bad node/tsx, syntax error, …); retrying on
            # other ports would just hide the error for ~40s. A lost port
            # race exits with _PORT_IN_USE_EXIT and falls through to advance.
            log.error(f"Dashboard server exited with code {proc.returncode}; see its output above.")
            raise typer.Exit(1)
        _shutdown(proc)
        nxt = _find_free_port(final_port)
        if nxt is None:
            log.error("Could not find a free port.")
            raise typer.Exit(1)
        final_port = nxt
        proc = spawn(final_port)
    else:
        log.error(f"Server did not start after {_MAX_PORT_ATTEMPTS} attempts.")
        _shutdown(proc)
        raise typer.Exit(1)

    base = f"http://127.0.0.1:{final_port}"
    log.header("Iteris Dashboard")
    log.key_value({"Dashboard": f"{base}/logs", "Project": str(root), "PID": str(proc.pid)})
    log.step(f"Stop: Ctrl-C  (or kill {proc.pid})")
    if open_browser:
        try:
            webbrowser.open(f"{base}/logs")
        except Exception:
            pass

    def _on_term(_signum, _frame):
        _shutdown(proc)
        raise SystemExit(0)

    try:
        signal.signal(signal.SIGTERM, _on_term)
    except (ValueError, OSError):
        pass

    try:
        proc.wait()
    except KeyboardInterrupt:
        _shutdown(proc)


def _shutdown(proc: subprocess.Popen, timeout: float = 5.0) -> None:
    if proc.poll() is not None:
        return
    try:
        if os.name != "nt":
            os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
        else:
            proc.terminate()
    except (ProcessLookupError, OSError):
        return
    try:
        proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        try:
            if os.name != "nt":
                os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
            else:
                proc.kill()
        except (ProcessLookupError, OSError):
            pass

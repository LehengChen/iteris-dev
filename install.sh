#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

info() { printf '[ITERIS] %s\n' "$*"; }
warn() { printf '[ITERIS] WARN: %s\n' "$*" >&2; }
die() { printf '[ITERIS] ERROR: %s\n' "$*" >&2; exit 1; }
have() { command -v "$1" >/dev/null 2>&1; }

OS="$(uname -s)"
SUDO=""
if [ "$(id -u)" -ne 0 ] && have sudo; then
  SUDO="sudo"
fi

detect_manager() {
  if [ "$OS" = "Darwin" ] && have brew; then
    echo "brew"
  elif have apt-get; then
    echo "apt-get"
  elif have dnf; then
    echo "dnf"
  elif have yum; then
    echo "yum"
  elif have pacman; then
    echo "pacman"
  else
    echo ""
  fi
}

install_packages() {
  manager="$1"
  shift
  [ "$#" -gt 0 ] || return 0
  if [ "$(id -u)" -ne 0 ] && [ -z "$SUDO" ] && [ "$manager" != "brew" ]; then
    warn "Cannot install system packages without root or sudo."
    warn "Please install manually: $*"
    return 1
  fi
  case "$manager" in
    brew)
      brew install "$@"
      ;;
    apt-get)
      $SUDO apt-get update
      $SUDO apt-get install -y "$@"
      ;;
    dnf)
      $SUDO dnf install -y "$@"
      ;;
    yum)
      $SUDO yum install -y "$@"
      ;;
    pacman)
      $SUDO pacman -S --needed "$@"
      ;;
    *)
      return 1
      ;;
  esac
}

packages_for_missing_tools() {
  manager="$1"
  packages=()

  if ! have python3; then
    case "$manager" in
      brew) packages+=("python") ;;
      pacman) packages+=("python" "python-pip") ;;
      *) packages+=("python3" "python3-pip") ;;
    esac
  elif ! python3 -m pip --version >/dev/null 2>&1; then
    case "$manager" in
      brew) packages+=("python") ;;
      pacman) packages+=("python-pip") ;;
      *) packages+=("python3-pip") ;;
    esac
  fi
  if have python3 && [ -z "${VIRTUAL_ENV:-}" ] && [ "${ITERIS_SYSTEM_PIP:-0}" != "1" ] && ! python3 -m venv -h >/dev/null 2>&1; then
    case "$manager" in
      apt-get) packages+=("python3-venv") ;;
    esac
  fi

  have git || packages+=("git")
  have rg || packages+=("ripgrep")
  have tmux || packages+=("tmux")
  if ! have node || ! have npm; then
    case "$manager" in
      brew) packages+=("node") ;;
      *) packages+=("nodejs" "npm") ;;
    esac
  fi

  printf '%s\n' "${packages[@]}"
}

manager="$(detect_manager)"
if [ "${ITERIS_SKIP_SYSTEM_DEPS:-0}" != "1" ]; then
  if [ -n "$manager" ]; then
    packages=()
    while IFS= read -r package; do
      [ -n "$package" ] && packages+=("$package")
    done < <(packages_for_missing_tools "$manager" | awk 'NF && !seen[$0]++')
    if [ "${#packages[@]}" -gt 0 ]; then
      info "Installing system dependencies with $manager: ${packages[*]}"
      install_packages "$manager" "${packages[@]}"
    else
      info "System dependencies already present."
    fi
  else
    warn "No supported package manager found. Install python3, pip, git, ripgrep, tmux, node, and npm manually."
  fi
else
  info "Skipping system dependency installation because ITERIS_SKIP_SYSTEM_DEPS=1."
fi

# The dashboard server runs via `node --import tsx`, which needs Node >= 18.
# Distro packages are often too old (Ubuntu 22.04 ships Node 12), so when the
# active node is missing or stale we provision Node 18 through nvm.
node_major() {
  have node || { echo 0; return; }
  local major
  major="$(node -v 2>/dev/null | sed -E 's/^v?([0-9]+).*/\1/')"
  case "$major" in
    ''|*[!0-9]*) echo 0 ;;
    *) echo "$major" ;;
  esac
}

publish_nvm_bin() {
  # nvm changes PATH only for the current shell. When this installer is run as
  # root or with a writable /usr/local/bin, expose the selected toolchain to
  # future non-login shells too.
  if [ -z "${NVM_BIN:-}" ] || [ ! -d "$NVM_BIN" ]; then
    return 0
  fi
  if [ "$(id -u)" -ne 0 ] && [ ! -w /usr/local/bin ]; then
    return 0
  fi
  mkdir -p /usr/local/bin 2>/dev/null || return 0
  for tool in node npm npx codex; do
    if [ -x "$NVM_BIN/$tool" ] || [ -L "$NVM_BIN/$tool" ]; then
      ln -sf "$NVM_BIN/$tool" "/usr/local/bin/$tool" 2>/dev/null || true
    fi
  done
}

ensure_node_20() {
  if [ "${ITERIS_SKIP_NODE_20:-0}" = "1" ]; then
    return 0
  fi
  if [ "$(node_major)" -ge 20 ] 2>/dev/null; then
    info "Node $(node -v) is new enough for the dashboard."
    return 0
  fi
  warn "Node >= 20 is required for the dashboard (fastify 5); current: $(have node && node -v || echo 'none')."
  export NVM_DIR="${NVM_DIR:-$HOME/.nvm}"
  if [ ! -s "$NVM_DIR/nvm.sh" ] && [ -s "/usr/local/nvm/nvm.sh" ]; then
    export NVM_DIR="/usr/local/nvm"
  fi
  if [ ! -s "$NVM_DIR/nvm.sh" ]; then
    info "Installing nvm into $NVM_DIR to provision Node 22."
    curl -fsSL https://raw.githubusercontent.com/nvm-sh/nvm/v0.39.7/install.sh | bash \
      || { warn "nvm install failed; install Node >= 20 manually for the dashboard."; return 1; }
  fi
  # nvm does not support `set -u`; it relies on unset variables defaulting to
  # empty (e.g. nvm.sh references EXIT_CODE before assigning it on the
  # already-installed path). Relax nounset while sourcing and driving nvm, then
  # restore the caller's strict mode.
  local _had_nounset=0
  case "$-" in *u*) _had_nounset=1 ;; esac
  set +u
  # shellcheck disable=SC1090
  . "$NVM_DIR/nvm.sh"
  if nvm install 22 && nvm alias default 22; then
    if [ -n "${NVM_BIN:-}" ]; then
      export PATH="$NVM_BIN:$PATH"
      publish_nvm_bin
    fi
    [ "$_had_nounset" = 1 ] && set -u
    info "Provisioned Node $(node -v) via nvm (default)."
  else
    [ "$_had_nounset" = 1 ] && set -u
    warn "Could not install Node 22 via nvm; the dashboard may not start."
    return 1
  fi
}

ensure_node_20 || true

if ! have python3; then
  die "python3 is not installed."
fi

if ! python3 - <<'PY' >/dev/null 2>&1
import sys
raise SystemExit(0 if sys.version_info >= (3, 10) else 1)
PY
then
  die "Python 3.10 or newer is required."
fi

if ! python3 -m pip --version >/dev/null 2>&1; then
  die "pip for python3 is not installed."
fi

if ! have codex; then
  if have npm; then
    info "Installing OpenAI Codex CLI with npm."
    npm_prefix="$(npm config get prefix 2>/dev/null || true)"
    npm_cmd="$(command -v npm)"
    if [ -n "$npm_prefix" ] && [ -w "$npm_prefix" ]; then
      npm install -g @openai/codex || die "Codex CLI install failed. Try: npm install -g @openai/codex"
    elif [ -n "$SUDO" ]; then
      $SUDO "$npm_cmd" install -g @openai/codex || die "Codex CLI install failed. Try: sudo npm install -g @openai/codex"
    elif [ "$(id -u)" -eq 0 ]; then
      npm install -g @openai/codex || die "Codex CLI install failed. Try: npm install -g @openai/codex"
    else
      warn "Global npm prefix is not writable: ${npm_prefix:-unknown}"
      warn "Install Codex manually with a writable npm prefix, or run: sudo npm install -g @openai/codex"
    fi
  else
    warn "npm is not installed, so Codex CLI cannot be installed automatically."
  fi
fi

publish_nvm_bin

if ! have codex; then
  warn "Codex CLI is still missing. Install it with: npm install -g @openai/codex"
else
  info "Codex CLI: $(codex --version 2>/dev/null || printf 'installed')"
fi

# Claude Code is the alternative executor (iteris run --executor claude). It is
# best-effort: a failed install must not abort the Iteris install, since codex
# alone is enough to run. Skip with ITERIS_SKIP_CLAUDE=1.
if [ "${ITERIS_SKIP_CLAUDE:-0}" != "1" ] && ! have claude; then
  if have npm; then
    info "Installing Claude Code CLI with npm (optional executor)."
    npm_prefix="$(npm config get prefix 2>/dev/null || true)"
    npm_cmd="$(command -v npm)"
    if [ -n "$npm_prefix" ] && [ -w "$npm_prefix" ]; then
      npm install -g @anthropic-ai/claude-code || warn "Claude Code install failed (optional). Try: npm install -g @anthropic-ai/claude-code"
    elif [ -n "$SUDO" ]; then
      $SUDO "$npm_cmd" install -g @anthropic-ai/claude-code || warn "Claude Code install failed (optional). Try: sudo npm install -g @anthropic-ai/claude-code"
    elif [ "$(id -u)" -eq 0 ]; then
      npm install -g @anthropic-ai/claude-code || warn "Claude Code install failed (optional). Try: npm install -g @anthropic-ai/claude-code"
    else
      warn "Global npm prefix is not writable: ${npm_prefix:-unknown}; skipping optional Claude Code install."
    fi
    publish_nvm_bin
  fi
fi

if have claude; then
  info "Claude Code CLI: $(claude --version 2>/dev/null || printf 'installed')"
fi

info "Installing Iteris Python package."
# The package version is unchanged across many code commits, so a plain
# `pip install` no-ops and silently leaves stale code deployed. --force-reinstall
# makes the deploy actually swap the binary; the build commit is stamped below so
# the deployed venv can report/skew-check exactly what it is running.
ITERIS_BUILD_COMMIT="$(git -C "$ROOT_DIR" rev-parse HEAD 2>/dev/null || echo unknown)"
rm -rf "$ROOT_DIR/build" "$ROOT_DIR/dist" "$ROOT_DIR/src/iteris.egg-info"
if [ -z "${VIRTUAL_ENV:-}" ] && [ "${ITERIS_SYSTEM_PIP:-0}" != "1" ]; then
  VENV_DIR="${ITERIS_VENV_DIR:-$HOME/.local/share/iteris/venv}"
  info "Using isolated Python environment: $VENV_DIR"
  python3 -m venv "$VENV_DIR" || die "Could not create venv. On Ubuntu/Debian, install python3-venv and rerun."
  "$VENV_DIR/bin/python" -m pip install --upgrade pip
  "$VENV_DIR/bin/python" -m pip install --force-reinstall "$ROOT_DIR"
  mkdir -p "$HOME/.local/bin"
  ln -sf "$VENV_DIR/bin/iteris" "$HOME/.local/bin/iteris"
  ITERIS_PY="$VENV_DIR/bin/python"
else
  info "Using the active Python environment."
  python3 -m pip install --force-reinstall "$ROOT_DIR"
  ITERIS_PY="$(command -v python3)"
fi
"$ITERIS_PY" - "$ITERIS_BUILD_COMMIT" <<'PYSTAMP' || echo "[iteris] WARN: could not stamp build commit"
import sys, pathlib, iteris
p = pathlib.Path(iteris.__file__).resolve().parent / "_build_info.py"
p.write_text('"""Deploy provenance - stamped by install.sh."""\n\nfrom __future__ import annotations\n\nBUILD_COMMIT = %r\n' % sys.argv[1])
print("[iteris] stamped BUILD_COMMIT", sys.argv[1])
PYSTAMP

if ! have iteris; then
  if [ -z "${VIRTUAL_ENV:-}" ] && [ "${ITERIS_SYSTEM_PIP:-0}" != "1" ]; then
    iteris_bin_dir="$HOME/.local/bin"
  else
    user_base="$(python3 -m site --user-base 2>/dev/null || true)"
    iteris_bin_dir="${user_base:-$HOME/.local}/bin"
  fi

  shell_name="$(basename "${SHELL:-}")"
  case "$shell_name" in
    zsh)
      shell_profile="$HOME/.zshrc"
      ;;
    bash)
      if [ "$OS" = "Darwin" ]; then
        shell_profile="$HOME/.bash_profile"
      else
        shell_profile="$HOME/.bashrc"
      fi
      ;;
    *)
      shell_profile="$HOME/.profile"
      ;;
  esac

  warn "The iteris command was installed, but its bin directory is not on PATH."
  cat <<EOF

[ITERIS] Run these commands, then try Iteris again:

  echo 'export PATH="$iteris_bin_dir:\$PATH"' >> "$shell_profile"
  source "$shell_profile"
  iteris doctor

[ITERIS] If you use a different shell, add this directory to PATH manually:

  $iteris_bin_dir

EOF
  exit 1
fi

if iteris doctor; then
  ITERIS_DOCTOR_OK=1
else
  ITERIS_DOCTOR_OK=0
fi

if have codex; then
  cat <<'EOF'

[ITERIS] Codex CLI is installed. If this is the first install on this machine,
[ITERIS] run `codex` once in a terminal and complete login/authorization before
[ITERIS] starting `iteris run`.
EOF
fi

if have claude; then
  cat <<'EOF'

[ITERIS] Claude Code CLI is installed (optional executor). To use it, run
[ITERIS] `claude` once to complete login, then `iteris run --executor claude`
[ITERIS] (or export ITERIS_EXECUTOR=claude).
EOF
fi

if [ "$ITERIS_DOCTOR_OK" = "1" ]; then
  cat <<'EOF'

[ITERIS] Iteris is installed and the environment check passed.
[ITERIS] Recommended next steps:

  1. Create a separate project directory:

       mkdir -p ./MyProblem
       cd ./MyProblem

  2. Start the interactive monitor:

       iteris monitor

[ITERIS] Monitor can guide project creation, setup checks, dashboard use, and
[ITERIS] evolve-family workflows from inside that project directory.
EOF
else
  cat <<'EOF'

[ITERIS] Iteris was installed, but `iteris doctor` reported issues above.
[ITERIS] Fix the reported environment items, then run:

  iteris doctor

[ITERIS] Once it passes, create a separate project directory and run:

  iteris monitor
EOF
fi

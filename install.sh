#!/usr/bin/env sh
# aibridge installer for macOS and Linux.
#
# Usage:
#   curl -fsSL https://raw.githubusercontent.com/yudhaharsanto/aibridge/main/install.sh | sh
#
# What it does:
#   1. Installs `uv` (from Astral) if not already present. uv is a single-file
#      Python package manager that can also bootstrap its own Python runtime,
#      so you don't need Python pre-installed.
#   2. Installs aibridge as an isolated tool via `uv tool install`.
#   3. Makes sure ~/.local/bin is on your PATH so `aibridge` is callable.
#
# Environment overrides:
#   AIBRIDGE_REF     git ref (branch/tag/sha) to install. Default: main
#   AIBRIDGE_REPO    override repo url. Default: https://github.com/yudhaharsanto/aibridge.git
#   AIBRIDGE_SKIP_SETUP=1  skip the automatic `aibridge setup` step

set -eu

REPO_URL="${AIBRIDGE_REPO:-https://github.com/yudhaharsanto/aibridge.git}"
REF="${AIBRIDGE_REF:-main}"

# ---- helpers ---------------------------------------------------------------

RED=""
GREEN=""
YELLOW=""
BLUE=""
BOLD=""
RESET=""
if [ -t 1 ] && command -v tput >/dev/null 2>&1 && [ "$(tput colors 2>/dev/null || echo 0)" -ge 8 ]; then
    RED="$(tput setaf 1)"
    GREEN="$(tput setaf 2)"
    YELLOW="$(tput setaf 3)"
    BLUE="$(tput setaf 4)"
    BOLD="$(tput bold)"
    RESET="$(tput sgr0)"
fi

info()  { printf "%s==>%s %s\n" "${BLUE}${BOLD}" "${RESET}" "$1"; }
ok()    { printf "%s✔%s %s\n"  "${GREEN}${BOLD}" "${RESET}" "$1"; }
warn()  { printf "%s!%s %s\n"  "${YELLOW}${BOLD}" "${RESET}" "$1"; }
err()   { printf "%s✘%s %s\n"  "${RED}${BOLD}"   "${RESET}" "$1" >&2; }

need_cmd() {
    if ! command -v "$1" >/dev/null 2>&1; then
        err "required command not found: $1"
        exit 1
    fi
}

# ---- preflight -------------------------------------------------------------

OS="$(uname -s)"
case "$OS" in
    Darwin|Linux) : ;;
    *)
        err "unsupported OS: $OS"
        err "this installer supports macOS and Linux."
        err "for Windows, use install.ps1 instead."
        exit 1
        ;;
esac

need_cmd uname
if ! command -v curl >/dev/null 2>&1 && ! command -v wget >/dev/null 2>&1; then
    err "need curl or wget installed to continue"
    exit 1
fi

printf "\n%s%saibridge installer%s\n\n" "${BOLD}" "${BLUE}" "${RESET}"

# ---- step 1: ensure uv -----------------------------------------------------

if command -v uv >/dev/null 2>&1; then
    ok "uv already installed ($(uv --version 2>/dev/null || echo 'unknown version'))"
else
    info "installing uv (Astral) — a fast, isolated Python tool manager..."
    if command -v curl >/dev/null 2>&1; then
        curl -LsSf https://astral.sh/uv/install.sh | sh
    else
        wget -qO- https://astral.sh/uv/install.sh | sh
    fi

    # uv installs to ~/.local/bin (or $XDG_BIN_HOME). Make sure it's on PATH
    # for the remainder of this script even before the user reloads their shell.
    if [ -z "${XDG_BIN_HOME:-}" ]; then
        UV_BIN_DIR="$HOME/.local/bin"
    else
        UV_BIN_DIR="$XDG_BIN_HOME"
    fi
    case ":$PATH:" in
        *":$UV_BIN_DIR:"*) : ;;
        *) PATH="$UV_BIN_DIR:$PATH" ;;
    esac
    export PATH

    if ! command -v uv >/dev/null 2>&1; then
        err "uv install finished but 'uv' is still not on PATH."
        err "try opening a new shell and re-running this installer."
        exit 1
    fi
    ok "uv installed"
fi

# ---- step 2: install aibridge ---------------------------------------------

info "installing aibridge from ${REPO_URL}@${REF}..."
# `uv tool install` gives aibridge its own isolated venv and puts a launcher
# on PATH. --force lets users re-run the installer to upgrade.
uv tool install --force "git+${REPO_URL}@${REF}"

# `uv tool install` drops shims in ~/.local/bin (or equivalent). Make sure
# future shells can see them too.
uv tool update-shell >/dev/null 2>&1 || true

if ! command -v aibridge >/dev/null 2>&1; then
    # Fallback: try the conventional uv tool bin dir for *this* shell.
    TOOL_BIN="$HOME/.local/bin"
    case ":$PATH:" in
        *":$TOOL_BIN:"*) : ;;
        *) PATH="$TOOL_BIN:$PATH" ;;
    esac
    export PATH
fi

if command -v aibridge >/dev/null 2>&1; then
    ok "aibridge installed: $(command -v aibridge)"
else
    warn "aibridge installed, but not yet on PATH in this shell."
    warn "open a new terminal, or run:  export PATH=\"\$HOME/.local/bin:\$PATH\""
fi

# ---- step 3: one-time setup -----------------------------------------------

if [ "${AIBRIDGE_SKIP_SETUP:-0}" = "1" ]; then
    info "skipping 'aibridge setup' (AIBRIDGE_SKIP_SETUP=1)"
else
    if command -v aibridge >/dev/null 2>&1; then
        info "running one-time setup (downloads Camoufox browser, ~200 MB)..."
        if ! aibridge setup; then
            warn "'aibridge setup' failed. You can re-run it manually later:"
            warn "    aibridge setup"
        fi
    fi
fi

# ---- done ------------------------------------------------------------------

printf "\n%s%sdone.%s\n\n" "${GREEN}" "${BOLD}" "${RESET}"
cat <<EOF
Next steps:

  1. Log in to the provider(s) you want:
       aibridge login monica
       aibridge login perplexity

  2. Start the background daemon(s):
       aibridge start all

  3. (optional) Install autostart on login:
       aibridge install-service monica
       aibridge install-service perplexity

  4. (optional) Register with 9router:
       aibridge register-9router monica
       aibridge register-9router perplexity

See 'aibridge --help' or https://github.com/yudhaharsanto/aibridge for details.
EOF

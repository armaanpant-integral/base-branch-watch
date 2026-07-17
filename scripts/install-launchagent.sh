#!/bin/sh
# Install (or uninstall) the base-branch-watch LaunchAgent.
#
# Renders scripts/com.armaan.base-branch-watch.plist.template with an
# absolute interpreter path resolved at *install time* (never baked into a
# committed file — ARCHITECTURE.md Anti-Pattern 2 / Anti-Pattern 3), writes
# it to ~/Library/LaunchAgents/, and loads it via the modern
# `launchctl bootstrap`/`enable` pair. RunAtLoad+KeepAlive means launchd
# starts the app at login and respawns it after a crash.
#
# Usage:
#   scripts/install-launchagent.sh [path-to-python3-interpreter]
#       Install (or re-install) the LaunchAgent. If no interpreter path is
#       given, resolves one via `command -v python3`. The interpreter must
#       be able to `import base_branch_watch` (i.e. the package must already
#       be installed into it, e.g. `pip install -e .` in this repo's venv).
#
#   scripts/install-launchagent.sh --uninstall
#       Unload the LaunchAgent (`launchctl bootout`) and remove the
#       installed plist. Does not touch config.json/state.json.

set -eu

LABEL="com.armaan.base-branch-watch"
SCRIPT_DIR=$(cd "$(dirname "$0")" && pwd)
REPO_ROOT=$(cd "$SCRIPT_DIR/.." && pwd)
TEMPLATE="$SCRIPT_DIR/com.armaan.base-branch-watch.plist.template"
PLIST_DEST="$HOME/Library/LaunchAgents/$LABEL.plist"
LOG_DIR="$HOME/Library/Application Support/base-branch-watch"
GUI_DOMAIN="gui/$(id -u)"

uninstall() {
    echo "Unloading LaunchAgent (if loaded): $LABEL"
    launchctl bootout "$GUI_DOMAIN/$LABEL" >/dev/null 2>&1 || true
    if [ -f "$PLIST_DEST" ]; then
        rm -f "$PLIST_DEST"
        echo "Removed $PLIST_DEST"
    else
        echo "No installed plist found at $PLIST_DEST (nothing to remove)."
    fi
    echo "Uninstall complete. config.json/state.json were not touched."
}

install() {
    python_bin="${1:-}"
    if [ -z "$python_bin" ]; then
        python_bin=$(command -v python3 || true)
    fi
    if [ -z "$python_bin" ]; then
        echo "ERROR: no python3 interpreter found on PATH and none given as an argument." >&2
        echo "Usage: $0 [path-to-python3-interpreter]" >&2
        exit 1
    fi
    case "$python_bin" in
        /*) : ;; # already absolute
        *)
            resolved=$(command -v "$python_bin" || true)
            if [ -z "$resolved" ]; then
                echo "ERROR: '$python_bin' is not an absolute path and was not found on PATH." >&2
                exit 1
            fi
            python_bin="$resolved"
            ;;
    esac

    if ! "$python_bin" -c "import base_branch_watch" >/dev/null 2>&1; then
        echo "ERROR: $python_bin cannot import base_branch_watch." >&2
        echo "Install the package into this interpreter first (e.g. 'pip install -e .'" >&2
        echo "from this repo with that interpreter's venv active), or pass the correct" >&2
        echo "interpreter path as an argument to this script." >&2
        exit 1
    fi

    echo "Baking in interpreter: $python_bin"
    echo "(confirmed it can import base_branch_watch — this is the exact interpreter"
    echo " launchd will spawn directly, unattended, at every login)"

    # launchd runs this agent under its own bare default PATH
    # (/usr/bin:/bin:/usr/sbin:/sbin), which excludes /opt/homebrew/bin and
    # similar. That starves any PATH-dependent subprocess dependency (e.g.
    # `gh`, resolved via shutil.which at import in pr_status.py) of a real
    # PATH, producing false "not installed" results. Bake the installing
    # shell's PATH into the plist so launchd's spawned process sees the same
    # PATH the installer had. Escape sed metacharacters (&, #, \) exactly as
    # install-pre-push-hook.sh escapes python_bin (WR-03) - the "#" matters
    # because "#" is this script's sed substitution delimiter. XML-unsafe
    # characters (<, >, &) inside a PATH component are intentionally left
    # unescaped: they are pathological on a dev machine and out of scope for
    # this installer.
    echo "Baking in PATH: $PATH"
    path_escaped=$(printf '%s\n' "$PATH" | sed 's/[&#\\]/\\&/g')

    mkdir -p "$HOME/Library/LaunchAgents"
    mkdir -p "$LOG_DIR"

    sed \
        -e "s#__PYTHON__#$python_bin#g" \
        -e "s#__WORKDIR__#$REPO_ROOT#g" \
        -e "s#__LOGDIR__#$LOG_DIR#g" \
        -e "s#__PATH__#$path_escaped#g" \
        "$TEMPLATE" > "$PLIST_DEST"
    echo "Wrote $PLIST_DEST"

    # Idempotent re-install: unload any previously-loaded instance first.
    launchctl bootout "$GUI_DOMAIN/$LABEL" >/dev/null 2>&1 || true

    launchctl bootstrap "$GUI_DOMAIN" "$PLIST_DEST"
    launchctl enable "$GUI_DOMAIN/$LABEL"

    echo "LaunchAgent installed and loaded: $LABEL"
    echo "Confirm it is running with: launchctl list | grep $LABEL"
    echo ""
    echo "Note: KeepAlive respawns the process after a crash or 'kill' — the"
    echo "in-menu Quit item is therefore not a durable stop. To fully disable:"
    echo "  launchctl bootout $GUI_DOMAIN/$LABEL"
    echo "or re-run this script with --uninstall."
}

if [ "${1:-}" = "--uninstall" ]; then
    uninstall
else
    install "${1:-}"
fi

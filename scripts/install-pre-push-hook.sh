#!/bin/sh
# Install (or uninstall) the bbwatch pre-push conflict-gate hook into a repo.
#
# Renders scripts/pre-push-hook.sh.template with an absolute interpreter path
# resolved at *install time* (mirrors install-launchagent.sh's interpreter-
# baking discipline -- D-02), writes it into the repo's resolved hooks
# directory (always resolved via `git rev-parse --git-path hooks`, joined
# onto the repo root if relative -- honors any core.hooksPath override,
# 03-RESEARCH.md Pitfall 1, never a hardcoded default path), and refuses to
# clobber a pre-existing hook that isn't already bbwatch-managed (D-03).
# Uninstall only ever removes a hook it itself wrote (D-04). Re-running
# install against an already-marked hook simply rewrites it -- this is what
# makes the menubar app's startup backfill loop safe to run on every launch.
#
# Usage:
#   scripts/install-pre-push-hook.sh <repo_path> [path-to-python3-interpreter]
#       Install (or re-install/backfill) the pre-push hook into <repo_path>.
#       If no interpreter path is given, resolves one via `command -v python3`.
#       The interpreter must be able to `import base_branch_watch` (i.e. the
#       package must already be installed into it, e.g. `pip install -e .`).
#
#   scripts/install-pre-push-hook.sh --uninstall <repo_path>
#       Remove the installed pre-push hook from <repo_path>, but only if it
#       carries the bbwatch-managed marker; a foreign hook is left untouched.

set -eu

SCRIPT_DIR=$(cd "$(dirname "$0")" && pwd)
TEMPLATE="$SCRIPT_DIR/pre-push-hook.sh.template"
MARKER="# bbwatch-managed"

hooks_dir_for() {
    repo_path="$1"
    raw=$(git -C "$repo_path" rev-parse --git-path hooks 2>/dev/null) || {
        echo "ERROR: '$repo_path' does not look like a git repository (rev-parse failed)." >&2
        exit 1
    }
    case "$raw" in
        /*) printf '%s\n' "$raw" ;;
        *)
            repo_root=$(cd "$repo_path" && pwd)
            printf '%s\n' "$repo_root/$raw"
            ;;
    esac
}

is_bbwatch_hook() {
    hook_path="$1"
    [ -f "$hook_path" ] || return 1
    sed -n '1,2p' "$hook_path" | grep -qxF "$MARKER"
}

uninstall() {
    repo_path="$1"
    hooks_dir=$(hooks_dir_for "$repo_path")
    hook_path="$hooks_dir/pre-push"
    if [ ! -f "$hook_path" ]; then
        echo "No pre-push hook found at $hook_path (nothing to remove)."
        return 0
    fi
    if ! is_bbwatch_hook "$hook_path"; then
        echo "Leaving $hook_path untouched -- it is not a bbwatch-managed hook."
        return 0
    fi
    rm -f "$hook_path"
    echo "Removed bbwatch-managed hook: $hook_path"
}

install() {
    repo_path="$1"
    python_bin="${2:-}"
    if [ -z "$python_bin" ]; then
        python_bin=$(command -v python3 || true)
    fi
    if [ -z "$python_bin" ]; then
        echo "ERROR: no python3 interpreter found on PATH and none given as an argument." >&2
        echo "Usage: $0 <repo_path> [path-to-python3-interpreter]" >&2
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

    hooks_dir=$(hooks_dir_for "$repo_path")
    hook_path="$hooks_dir/pre-push"

    if [ -f "$hook_path" ] && ! is_bbwatch_hook "$hook_path"; then
        echo "ERROR: $hook_path already exists and is not bbwatch-managed." >&2
        echo "Refusing to overwrite a hook bbwatch did not install." >&2
        echo "To use bbwatch's pre-push gate alongside your existing hook, add a line" >&2
        echo "to your existing $hook_path that execs/chains into:" >&2
        echo "  \"$python_bin\" -m base_branch_watch.hook \"\$@\"" >&2
        exit 1
    fi

    mkdir -p "$hooks_dir"
    # Escape sed metacharacters (including our own "#" delimiter) in the
    # interpreter path before interpolating it as replacement text -- an
    # unescaped "#"/"&"/"\" in python_bin would otherwise corrupt the
    # substitution (WR-03).
    python_bin_escaped=$(printf '%s\n' "$python_bin" | sed 's/[&#\\]/\\&/g')
    sed -e "s#__PYTHON__#$python_bin_escaped#g" "$TEMPLATE" > "$hook_path"
    chmod +x "$hook_path"
    echo "Installed bbwatch pre-push hook: $hook_path"
}

if [ "${1:-}" = "--uninstall" ]; then
    shift
    if [ -z "${1:-}" ]; then
        echo "Usage: $0 --uninstall <repo_path>" >&2
        exit 1
    fi
    uninstall "$1"
else
    if [ -z "${1:-}" ]; then
        echo "Usage: $0 <repo_path> [path-to-python3-interpreter]" >&2
        exit 1
    fi
    install "$1" "${2:-}"
fi

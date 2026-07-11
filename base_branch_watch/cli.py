"""Thin argparse porcelain over scripts/install-launchagent.sh and
scripts/install-pre-push-hook.sh.

Exposes `bbwatch install-agent` / `bbwatch uninstall-agent` (LaunchAgent) and
`bbwatch install-hook` / `bbwatch uninstall-hook` (pre-push conflict gate) so
users don't have to remember either script's path — same underlying
install/uninstall logic, no duplicated shell logic here (ARCHITECTURE.md's
cli.py role: a third porcelain over the same plumbing, not a second copy of
it).

NOTE (WR-03): SCRIPT_PATH/HOOK_SCRIPT_PATH are resolved relative to this
installed package's location, which only has a sibling `scripts/` directory
in an editable/source install (`pip install -e .` — see INSTALL.md's
Prerequisites). A non-editable install (`pip install .` from a copied
sdist/wheel) will not have `scripts/` alongside it; `_run_script` below fails
loudly with a message explaining this rather than silently misbehaving.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

SCRIPT_PATH = Path(__file__).resolve().parent.parent / "scripts" / "install-launchagent.sh"
HOOK_SCRIPT_PATH = Path(__file__).resolve().parent.parent / "scripts" / "install-pre-push-hook.sh"


def _run_script(args: list[str], script_path: Path = SCRIPT_PATH) -> int:
    if not script_path.exists():
        print(
            f"ERROR: install script not found at {script_path}\n"
            "`bbwatch install-agent`/`uninstall-agent`/`install-hook`/`uninstall-hook` "
            "require an editable/source install (`pip install -e .` from a repo "
            "checkout) so `scripts/` ships alongside the installed package — a "
            "non-editable install does not include it. Run the script directly from "
            "your repo checkout instead, or reinstall with `pip install -e .`.",
            file=sys.stderr,
        )
        return 1
    result = subprocess.run(["/bin/sh", str(script_path), *args])
    return result.returncode


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="bbwatch")
    subparsers = parser.add_subparsers(dest="command", required=True)

    install_parser = subparsers.add_parser(
        "install-agent", help="Install and load the launchd LaunchAgent (autostart + KeepAlive)."
    )
    install_parser.add_argument(
        "python_path",
        nargs="?",
        default=None,
        help="Absolute path to the python3 interpreter to bake into the LaunchAgent. "
        "Defaults to resolving python3 on PATH.",
    )

    subparsers.add_parser(
        "uninstall-agent", help="Unload the LaunchAgent and remove its installed plist."
    )

    install_hook_parser = subparsers.add_parser(
        "install-hook", help="Install the bbwatch pre-push conflict-gate hook into a repo."
    )
    install_hook_parser.add_argument(
        "repo_path", help="Path to the git repository to install the hook into."
    )
    install_hook_parser.add_argument(
        "python_path",
        nargs="?",
        default=None,
        help="Absolute path to the python3 interpreter to bake into the hook. "
        "Defaults to resolving python3 on PATH.",
    )

    uninstall_hook_parser = subparsers.add_parser(
        "uninstall-hook",
        help="Remove the bbwatch pre-push hook from a repo (only if bbwatch-managed).",
    )
    uninstall_hook_parser.add_argument(
        "repo_path", help="Path to the git repository to uninstall the hook from."
    )

    args = parser.parse_args(argv)

    if args.command == "install-agent":
        script_args = [args.python_path] if args.python_path else []
        return _run_script(script_args)
    if args.command == "uninstall-agent":
        return _run_script(["--uninstall"])
    if args.command == "install-hook":
        script_args = [args.repo_path] + ([args.python_path] if args.python_path else [])
        return _run_script(script_args, HOOK_SCRIPT_PATH)
    if args.command == "uninstall-hook":
        return _run_script(["--uninstall", args.repo_path], HOOK_SCRIPT_PATH)

    parser.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())

"""Thin argparse porcelain over scripts/install-launchagent.sh.

Exposes `bbwatch install-agent` / `bbwatch uninstall-agent` so users don't
have to remember the script's path — same underlying install/uninstall
logic, no duplicated shell logic here (ARCHITECTURE.md's cli.py role: a
third porcelain over the same plumbing, not a second copy of it).
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

SCRIPT_PATH = Path(__file__).resolve().parent.parent / "scripts" / "install-launchagent.sh"


def _run_script(args: list[str]) -> int:
    if not SCRIPT_PATH.exists():
        print(f"ERROR: install script not found at {SCRIPT_PATH}", file=sys.stderr)
        return 1
    result = subprocess.run(["/bin/sh", str(SCRIPT_PATH), *args])
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

    args = parser.parse_args(argv)

    if args.command == "install-agent":
        script_args = [args.python_path] if args.python_path else []
        return _run_script(script_args)
    if args.command == "uninstall-agent":
        return _run_script(["--uninstall"])

    parser.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())

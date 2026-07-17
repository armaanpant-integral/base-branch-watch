"""Exercises scripts/install-launchagent.sh's plist rendering against a
sandboxed HOME, proving the installer bakes the installing shell's PATH into
a new EnvironmentVariables dict (fixes the `gh` NOT_INSTALLED false positive
under launchd's bare default PATH: /usr/bin:/bin:/usr/sbin:/sbin) while
preserving every pre-existing plist key.

Mirrors tests/test_hook_install.py's convention: shells the real
install-launchagent.sh, no mocking of the script under test. HOME is
sandboxed to tmp_path so PLIST_DEST never lands under the real
~/Library/LaunchAgents, and a fake launchctl stub neutralizes the script's
bootout/bootstrap/enable calls so real launchd is never touched.
"""

from __future__ import annotations

import os
import plistlib
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
INSTALL_SCRIPT = REPO_ROOT / "scripts" / "install-launchagent.sh"

LABEL = "com.armaan.base-branch-watch"


def _install(tmp_path: Path) -> tuple[subprocess.CompletedProcess[str], str]:
    """Runs the real install script against a sandboxed HOME plus a fake
    launchctl on PATH. Returns (result, the exact PATH the script saw)."""
    tmp_bin = tmp_path / "bin"
    tmp_bin.mkdir()
    launchctl_stub = tmp_bin / "launchctl"
    launchctl_stub.write_text("#!/bin/sh\nexit 0\n")
    launchctl_stub.chmod(0o755)

    sandbox_path = f"{tmp_bin}{os.pathsep}{os.environ['PATH']}"
    env = dict(os.environ)
    env["HOME"] = str(tmp_path)
    env["PATH"] = sandbox_path

    result = subprocess.run(
        ["/bin/sh", str(INSTALL_SCRIPT), sys.executable],
        capture_output=True,
        text=True,
        timeout=10,
        env=env,
    )
    return result, sandbox_path


def _rendered_plist(tmp_path: Path) -> dict:
    plist_path = tmp_path / "Library" / "LaunchAgents" / f"{LABEL}.plist"
    with plist_path.open("rb") as f:
        return plistlib.load(f)


def test_environment_variables_path_matches_installing_shell(tmp_path):
    result, sandbox_path = _install(tmp_path)

    assert result.returncode == 0, result.stderr
    plist = _rendered_plist(tmp_path)

    assert plist["EnvironmentVariables"]["PATH"] == sandbox_path


def test_preexisting_keys_survive_the_change(tmp_path):
    result, _sandbox_path = _install(tmp_path)

    assert result.returncode == 0, result.stderr
    plist = _rendered_plist(tmp_path)

    assert plist["Label"] == LABEL
    assert plist["ProgramArguments"][-1] == "base_branch_watch"
    assert plist["WorkingDirectory"] == str(REPO_ROOT)
    assert plist["RunAtLoad"] is True
    assert plist["KeepAlive"] is True
    assert plist["StandardOutPath"].endswith("launchd.stdout.log")
    assert plist["StandardErrorPath"].endswith("launchd.stderr.log")

"""bbwatch install-hook / uninstall-hook: argparse porcelain over core.hook_install."""

from __future__ import annotations

import sys
from pathlib import Path

from base_branch_watch import cli
from base_branch_watch.core import git_ops


def _hook_file(repo_path: str) -> Path:
    return Path(git_ops.hooks_path(repo_path)) / "pre-push"


def test_install_hook_command_installs_and_reports(fixture_repos, capsys):
    _origin, clone_path = fixture_repos

    code = cli.main(["install-hook", clone_path, sys.executable])

    assert code == 0
    assert _hook_file(clone_path).is_file()
    assert "Installed bbwatch pre-push hook" in capsys.readouterr().out


def test_install_hook_command_reports_refusal_on_stderr_with_exit_1(fixture_repos, capsys):
    _origin, clone_path = fixture_repos
    hook_file = _hook_file(clone_path)
    hook_file.parent.mkdir(parents=True, exist_ok=True)
    hook_file.write_text("#!/bin/sh\necho mine\n")

    code = cli.main(["install-hook", clone_path, sys.executable])

    assert code == 1
    assert "not bbwatch-managed" in capsys.readouterr().err


def test_uninstall_hook_command_removes_a_managed_hook(fixture_repos, capsys):
    _origin, clone_path = fixture_repos
    cli.main(["install-hook", clone_path, sys.executable])

    code = cli.main(["uninstall-hook", clone_path])

    assert code == 0
    assert not _hook_file(clone_path).exists()
    assert "Removed bbwatch-managed hook" in capsys.readouterr().out


def test_hook_commands_do_not_need_the_scripts_directory(fixture_repos, monkeypatch):
    """WR-03: a non-editable install has no scripts/ - hook commands must still work."""
    _origin, clone_path = fixture_repos
    monkeypatch.setattr(cli, "SCRIPT_PATH", Path("/nonexistent/install-launchagent.sh"))

    assert cli.main(["install-hook", clone_path, sys.executable]) == 0

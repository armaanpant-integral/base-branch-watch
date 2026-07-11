"""e2e + unit coverage of the `python3 -m base_branch_watch.hook` entry point.

No mocking of git itself -- exercises real tmp git repos per the project-wide
convention (see tests/conftest.py). The only thing monkeypatched is
`builtins.open` (to simulate a no-controlling-terminal /dev/tty per
RESEARCH.md Pattern 3) and `sys.stdin` (git repurposes stdin for pre-push
ref-update data, so tests feed it the exact captured protocol strings).
"""

from __future__ import annotations

import builtins
import io

from base_branch_watch import hook
from base_branch_watch.core import config as config_module
from base_branch_watch.core import git_ops
from base_branch_watch.core.models import RepoConfig

ZERO_SHA = "0" * 40


def _configure_repo(clone_path: str, base_branch: str) -> None:
    cfg = config_module.AppConfig(
        repos=[RepoConfig(repo_path=clone_path, base_branches=[base_branch])]
    )
    config_module.save_config(cfg)


def _pre_push_line(branch: str, local_sha: str) -> str:
    return f"refs/heads/{branch} {local_sha} refs/heads/{branch} {ZERO_SHA}\n"


def _patch_no_tty(monkeypatch) -> dict:
    """Simulate `open('/dev/tty')` raising OSError (no controlling terminal),
    while passing every other path through to the real `open`. Returns a
    dict tracking whether /dev/tty was ever attempted."""
    tty_opened = {"called": False}
    real_open = builtins.open

    def fake_open(path, *args, **kwargs):
        if path == "/dev/tty":
            tty_opened["called"] = True
            raise OSError(6, "Device not configured")
        return real_open(path, *args, **kwargs)

    monkeypatch.setattr("builtins.open", fake_open)
    return tty_opened


def test_hook_gate_aborts_on_conflict_without_tty(
    fixture_repos_conflict_overlap, default_branch_name, bbw_config_dir, monkeypatch, capsys
):
    _origin, clone_path = fixture_repos_conflict_overlap
    _configure_repo(clone_path, default_branch_name)
    local_sha = git_ops.resolve_ref(clone_path, "HEAD")
    assert local_sha is not None

    monkeypatch.setattr("sys.stdin", io.StringIO(_pre_push_line(default_branch_name, local_sha)))
    monkeypatch.chdir(clone_path)
    _patch_no_tty(monkeypatch)

    exit_code = hook.main(["origin", "git@example.com:test.git"])

    assert exit_code != 0
    captured = capsys.readouterr()
    assert default_branch_name in captured.out
    assert "⚠️" in captured.out


def test_hook_clean_repo_exits_zero_no_prompt(
    fixture_repos, default_branch_name, bbw_config_dir, monkeypatch, capsys
):
    _origin, clone_path = fixture_repos
    _configure_repo(clone_path, default_branch_name)
    local_sha = git_ops.resolve_ref(clone_path, "HEAD")
    assert local_sha is not None

    monkeypatch.setattr("sys.stdin", io.StringIO(_pre_push_line(default_branch_name, local_sha)))
    monkeypatch.chdir(clone_path)
    tty_opened = _patch_no_tty(monkeypatch)

    exit_code = hook.main(["origin", "git@example.com:test.git"])

    assert exit_code == 0
    assert tty_opened["called"] is False
    captured = capsys.readouterr()
    assert default_branch_name in captured.out
    assert "incoming commit" in captured.out


def test_hook_skip_gate_env_bypasses(
    fixture_repos_conflict_overlap, default_branch_name, bbw_config_dir, monkeypatch, capsys
):
    _origin, clone_path = fixture_repos_conflict_overlap
    _configure_repo(clone_path, default_branch_name)
    local_sha = git_ops.resolve_ref(clone_path, "HEAD")
    assert local_sha is not None

    monkeypatch.setattr("sys.stdin", io.StringIO(_pre_push_line(default_branch_name, local_sha)))
    monkeypatch.chdir(clone_path)
    monkeypatch.setenv("BBWATCH_SKIP_GATE", "1")
    tty_opened = _patch_no_tty(monkeypatch)

    exit_code = hook.main(["origin", "git@example.com:test.git"])

    assert exit_code == 0
    assert tty_opened["called"] is False

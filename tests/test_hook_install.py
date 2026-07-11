"""Exercises scripts/install-pre-push-hook.sh (install/uninstall) against real
tmp git repos (tests/conftest.py's fixture_repos convention -- no mocking of
git itself), plus the menubar add/remove/backfill trigger points added in
Task 3.

Covers HOOK-01 (auto-install per watched repo), D-02 (baked, import-verified
interpreter), D-03 (foreign-hook safety -- never clobber a hook bbwatch
didn't write), D-04 (uninstall only ever removes a bbwatch-marked hook), and
install idempotency (what makes the startup backfill loop safe to run on
every launch).
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest
import rumps

from base_branch_watch.app import menubar as menubar_module
from base_branch_watch.core import git_ops
from base_branch_watch.core.models import RepoConfig

REPO_ROOT = Path(__file__).resolve().parent.parent
INSTALL_SCRIPT = REPO_ROOT / "scripts" / "install-pre-push-hook.sh"

MARKER = "# bbwatch-managed"


def _install(repo_path: str, python_bin: str | None = None) -> subprocess.CompletedProcess[str]:
    args = ["/bin/sh", str(INSTALL_SCRIPT), repo_path]
    if python_bin is not None:
        args.append(python_bin)
    return subprocess.run(args, capture_output=True, text=True, timeout=10)


def _uninstall(repo_path: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["/bin/sh", str(INSTALL_SCRIPT), "--uninstall", repo_path],
        capture_output=True,
        text=True,
        timeout=10,
    )


def _hook_path(repo_path: str) -> Path:
    resolved = git_ops.hooks_path(repo_path)
    assert resolved is not None
    return Path(resolved) / "pre-push"


def test_install_creates_marked_hook(fixture_repos):
    _origin, clone_path = fixture_repos

    result = _install(clone_path, sys.executable)

    assert result.returncode == 0, result.stderr
    hook_file = _hook_path(clone_path)
    assert hook_file.is_file()
    lines = hook_file.read_text().splitlines()
    assert lines[1] == MARKER
    assert "__PYTHON__" not in hook_file.read_text()
    assert sys.executable in hook_file.read_text()
    assert "-m base_branch_watch.hook" in hook_file.read_text()


def test_install_refuses_foreign_hook(fixture_repos):
    _origin, clone_path = fixture_repos
    hook_file = _hook_path(clone_path)
    hook_file.parent.mkdir(parents=True, exist_ok=True)
    foreign_contents = "#!/bin/sh\necho 'my own pre-push hook'\n"
    hook_file.write_text(foreign_contents)

    result = _install(clone_path, sys.executable)

    assert result.returncode != 0
    assert hook_file.read_text() == foreign_contents, "foreign hook must be left byte-identical"
    assert result.stderr, "should print an instructional message on refusal"


def test_install_honors_core_hooksPath(fixture_repos):
    _origin, clone_path = fixture_repos
    subprocess.run(
        ["/usr/bin/git", "-C", clone_path, "config", "core.hooksPath", ".myhooks"],
        check=True,
        capture_output=True,
        text=True,
        timeout=10,
    )

    result = _install(clone_path, sys.executable)

    assert result.returncode == 0, result.stderr
    custom_hook = Path(clone_path) / ".myhooks" / "pre-push"
    assert custom_hook.is_file()
    default_hook = Path(clone_path) / ".git" / "hooks" / "pre-push"
    assert not default_hook.exists()


def test_uninstall_removes_only_marked_hook(fixture_repos):
    _origin, clone_path = fixture_repos
    _install(clone_path, sys.executable)
    hook_file = _hook_path(clone_path)
    assert hook_file.is_file()

    result = _uninstall(clone_path)

    assert result.returncode == 0, result.stderr
    assert not hook_file.exists()


def test_uninstall_leaves_foreign_hook_untouched(fixture_repos):
    _origin, clone_path = fixture_repos
    hook_file = _hook_path(clone_path)
    hook_file.parent.mkdir(parents=True, exist_ok=True)
    foreign_contents = "#!/bin/sh\necho 'my own pre-push hook'\n"
    hook_file.write_text(foreign_contents)

    result = _uninstall(clone_path)

    assert result.returncode == 0, result.stderr
    assert hook_file.read_text() == foreign_contents


def test_install_is_idempotent(fixture_repos):
    _origin, clone_path = fixture_repos

    first = _install(clone_path, sys.executable)
    second = _install(clone_path, sys.executable)

    assert first.returncode == 0, first.stderr
    assert second.returncode == 0, second.stderr
    hook_file = _hook_path(clone_path)
    lines = hook_file.read_text().splitlines()
    assert lines[1] == MARKER
    assert hook_file.parent.exists()
    # exactly one "pre-push" hook file at the resolved path (not counting
    # git's own unrelated "pre-push.sample" placeholder), no duplication
    assert [p for p in hook_file.parent.iterdir() if p.name == "pre-push"] == [hook_file]


# -- menubar add/remove/backfill trigger points (Task 3) --------------------
#
# Monkeypatch-and-capture style, per tests/app/test_menubar.py -- constructs
# a real BaseBranchWatchApp (pure-Python/PyObjC object setup, never touches
# the NSApplication run loop) and captures calls to the hook trigger methods
# instead of letting them shell out for real.


class _FakeURL:
    def __init__(self, path: str) -> None:
        self._path = path

    def path(self) -> str:
        return self._path


class _FakePanel:
    def __init__(self, path: str) -> None:
        self._path = path

    def setCanChooseDirectories_(self, _value) -> None:
        pass

    def setCanChooseFiles_(self, _value) -> None:
        pass

    def setAllowsMultipleSelection_(self, _value) -> None:
        pass

    def setPrompt_(self, _value) -> None:
        pass

    def runModal(self) -> int:
        return 1

    def URLs(self) -> list[_FakeURL]:
        return [_FakeURL(self._path)]


def _patch_open_panel(monkeypatch, repo_path: str) -> None:
    class _FakeNSOpenPanel:
        @staticmethod
        def openPanel():
            return _FakePanel(repo_path)

    monkeypatch.setattr(menubar_module, "NSOpenPanel", _FakeNSOpenPanel)


def _patch_confirm_alert(monkeypatch) -> None:
    def fake_show_alert(title, message, ok="OK", cancel=None):
        return 1  # simulates clicking the confirm/ok button

    monkeypatch.setattr(
        menubar_module.BaseBranchWatchApp, "_show_alert", staticmethod(fake_show_alert)
    )


@pytest.fixture()
def app(bbw_config_dir):
    """A real BaseBranchWatchApp -- __init__ only does pure-Python/PyObjC
    object setup, and cfg.repos is empty (isolated, fresh BBW_CONFIG_DIR),
    so the startup backfill loop and check_all(None) are real but instant
    no-ops. Same fixture shape as tests/app/test_menubar.py's own `app`."""
    instance = menubar_module.BaseBranchWatchApp()
    yield instance
    instance.timer.stop()


def test_add_repo_triggers_hook_install(app, monkeypatch, fixture_repos):
    _origin, clone_path = fixture_repos
    _patch_open_panel(monkeypatch, clone_path)

    class FakeResp:
        clicked = True
        text = "main"

    fake_window = type("W", (), {"run": lambda self: FakeResp()})
    monkeypatch.setattr(rumps, "Window", lambda **kwargs: fake_window())

    captured: list[str] = []
    monkeypatch.setattr(app, "_install_hook_for", lambda repo_path: captured.append(repo_path))

    app._add_repo(None)

    assert captured == [clone_path]


def test_remove_repo_handler_triggers_hook_uninstall(app, monkeypatch):
    repo_path = "/tmp/some-repo-to-remove"
    app.cfg.repos = [RepoConfig(repo_path=repo_path, base_branches=["main"])]
    _patch_confirm_alert(monkeypatch)

    captured: list[str] = []
    monkeypatch.setattr(app, "_uninstall_hook_for", lambda repo_path: captured.append(repo_path))

    app._remove_repo_click_handler(repo_path)(None)

    assert captured == [repo_path]


def test_backfill_hooks_installs_once_per_configured_repo(app, monkeypatch):
    app.cfg.repos = [
        RepoConfig(repo_path="/tmp/repo-one", base_branches=["main"]),
        RepoConfig(repo_path="/tmp/repo-two", base_branches=["main"]),
    ]
    captured: list[str] = []
    monkeypatch.setattr(app, "_install_hook_for", lambda repo_path: captured.append(repo_path))

    app._backfill_hooks()

    assert captured == ["/tmp/repo-one", "/tmp/repo-two"]


def test_install_hook_for_bakes_sys_executable(app, monkeypatch):
    captured_args: list[list[str]] = []

    def fake_run(args, **kwargs):
        captured_args.append(args)
        return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

    monkeypatch.setattr(menubar_module.subprocess, "run", fake_run)

    app._install_hook_for("/tmp/some-repo")

    assert captured_args[-1][-1] == sys.executable


def test_install_hook_for_logs_on_nonzero_exit(app, monkeypatch):
    """CR-01 regression: a refused install (e.g. a foreign hook already
    present) must surface a [FAIL] log line, not fail silently."""

    def fake_run(args, **kwargs):
        return subprocess.CompletedProcess(
            args, 1, stdout="", stderr="refusing to overwrite foreign hook"
        )

    monkeypatch.setattr(menubar_module.subprocess, "run", fake_run)
    logged: list[str] = []
    monkeypatch.setattr(menubar_module.log, "append", lambda line: logged.append(line))

    app._install_hook_for("/tmp/some-repo")

    assert any(
        "[FAIL]" in line and "refusing to overwrite foreign hook" in line for line in logged
    )


def test_uninstall_hook_for_logs_on_nonzero_exit(app, monkeypatch):
    """CR-01 regression, uninstall side: a refused uninstall (e.g. the
    installed hook isn't bbwatch-managed) must surface a [FAIL] log line."""

    def fake_run(args, **kwargs):
        return subprocess.CompletedProcess(args, 1, stdout="", stderr="not a bbwatch-managed hook")

    monkeypatch.setattr(menubar_module.subprocess, "run", fake_run)
    logged: list[str] = []
    monkeypatch.setattr(menubar_module.log, "append", lambda line: logged.append(line))

    app._uninstall_hook_for("/tmp/some-repo")

    assert any(
        "[FAIL]" in line and "not a bbwatch-managed hook" in line for line in logged
    )

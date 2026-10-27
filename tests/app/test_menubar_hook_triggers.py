"""Menubar add/remove/backfill trigger points for the pre-push hook (HOOK-01).

Monkeypatch-and-capture style, per tests/app/test_menubar.py -- constructs a real
BaseBranchWatchApp (pure-Python/PyObjC object setup, never touches the NSApplication
run loop) and captures calls to core.hook_install instead of touching real repos.
The install/uninstall logic itself is tested in tests/core/test_hook_install.py.
"""

from __future__ import annotations

import sys

import pytest
import rumps

from base_branch_watch.app import menubar as menubar_module
from base_branch_watch.core import hook_install
from base_branch_watch.core.models import RepoConfig


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

    def setCollectionBehavior_(self, _value) -> None:
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

    class FakeAlertWindow:
        def setCollectionBehavior_(self, _value) -> None:
            pass

    class FakeAlert:
        def window(self) -> FakeAlertWindow:
            return FakeAlertWindow()

    class FakeWindow:
        def __init__(self) -> None:
            self._alert = FakeAlert()

        def run(self) -> FakeResp:
            return FakeResp()

    monkeypatch.setattr(rumps, "Window", lambda **kwargs: FakeWindow())

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
    calls: list[tuple[str, str | None]] = []

    def fake_install(repo_path, python_bin=None, **kwargs):
        calls.append((repo_path, python_bin))
        return hook_install.HookResult(ok=True, message="installed")

    monkeypatch.setattr(menubar_module.hook_install, "install", fake_install)

    app._install_hook_for("/tmp/some-repo")

    assert calls == [("/tmp/some-repo", sys.executable)]


def test_install_hook_for_logs_a_refused_install(app, monkeypatch):
    """CR-01 regression: a refused install (e.g. a foreign hook already
    present) must surface a [FAIL] log line, not fail silently."""
    refusal = hook_install.HookResult(ok=False, message="refusing to overwrite foreign hook")
    monkeypatch.setattr(menubar_module.hook_install, "install", lambda *a, **k: refusal)
    logged: list[str] = []
    monkeypatch.setattr(menubar_module.log, "append", lambda line: logged.append(line))

    app._install_hook_for("/tmp/some-repo")

    assert logged == [
        "[FAIL] pre-push hook install failed for /tmp/some-repo: refusing to overwrite foreign hook"
    ]


def test_install_hook_for_logs_nothing_on_success(app, monkeypatch):
    ok = hook_install.HookResult(ok=True, message="Installed bbwatch pre-push hook: /x")
    monkeypatch.setattr(menubar_module.hook_install, "install", lambda *a, **k: ok)
    logged: list[str] = []
    monkeypatch.setattr(menubar_module.log, "append", lambda line: logged.append(line))

    app._install_hook_for("/tmp/some-repo")

    assert logged == []


def test_uninstall_hook_for_logs_a_failed_uninstall(app, monkeypatch):
    """CR-01 regression, uninstall side."""
    failure = hook_install.HookResult(ok=False, message="not a git repository")
    monkeypatch.setattr(menubar_module.hook_install, "uninstall", lambda *a, **k: failure)
    logged: list[str] = []
    monkeypatch.setattr(menubar_module.log, "append", lambda line: logged.append(line))

    app._uninstall_hook_for("/tmp/some-repo")

    assert logged == [
        "[FAIL] pre-push hook uninstall failed for /tmp/some-repo: not a git repository"
    ]

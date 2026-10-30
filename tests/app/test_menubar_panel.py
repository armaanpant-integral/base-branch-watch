"""The dashboard panel's glue in menubar.py and panel_host.py (no real status item)."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from base_branch_watch.app import menubar, panel_host
from base_branch_watch.core.models import (
    BranchStatus,
    RepoStatus,
    StaleBranch,
    StaleKind,
    StatusKind,
)


@pytest.fixture()
def app(bbw_config_dir):
    instance = menubar.BaseBranchWatchApp()
    yield instance
    instance.timer.stop()


@pytest.fixture()
def logged(monkeypatch) -> list[str]:
    lines: list[str] = []
    monkeypatch.setattr(menubar.log, "append", lines.append)
    monkeypatch.setattr(panel_host.log, "append", lines.append)
    return lines


def _status(name="repo-a", repo_path=None):
    return RepoStatus(
        repo_path=repo_path or f"/tmp/{name}",
        name=name,
        current_branch="main",
        unpushed=0,
        branch_statuses=[
            BranchStatus(base="main", behind=0, ahead_of_base=0, kind=StatusKind.UP_TO_DATE)
        ],
    )


def _capture_alert(monkeypatch) -> dict:
    captured: dict = {}

    def fake_show_alert(title, message, ok="OK", cancel=None):
        captured["title"] = title
        captured["message"] = message
        return 1

    monkeypatch.setattr(menubar.BaseBranchWatchApp, "_show_alert", staticmethod(fake_show_alert))
    monkeypatch.setattr(menubar.BaseBranchWatchApp, "_activate", lambda self: None)
    return captured


# -- _show_stale_branches ---------------------------------------------------


def test_show_stale_branches_lists_branches_under_the_repo_name(app, monkeypatch):
    captured = _capture_alert(monkeypatch)
    status = _status("crypto-trading")
    app.statuses = {status.repo_path: status}
    app._stale_statuses = {
        status.repo_path: [
            StaleBranch(name="old-1", kind=StaleKind.GONE_MERGED),
            StaleBranch(name="old-2", kind=StaleKind.GONE_UNMERGED),
        ]
    }

    app._show_stale_branches(status.repo_path)

    assert captured["title"] == "crypto-trading"
    assert captured["message"].splitlines() == [
        "old-1 - remote deleted, merged",
        "old-2 - remote deleted, NOT merged (check before deleting)",
    ]


def test_show_stale_branches_unknown_repo_falls_back_to_basename_without_trailing_slash(
    app, monkeypatch
):
    captured = _capture_alert(monkeypatch)

    app._show_stale_branches("/some/where/gone-repo/")

    assert captured == {"title": "gone-repo", "message": "No stale branches."}


# -- _install_panel ---------------------------------------------------------


def test_install_panel_failure_logs_and_leaves_no_host(app, monkeypatch, logged):
    monkeypatch.setattr(app, "_nsapp", SimpleNamespace(nsstatusitem=object()), raising=False)

    def boom(*args, **kwargs):
        raise RuntimeError("no button")

    monkeypatch.setattr(menubar, "PanelHost", boom)

    app._install_panel(None)

    assert app._panel_host is None
    assert any("dashboard panel install raised" in line for line in logged)


def test_install_panel_hands_the_host_live_state_not_a_snapshot(app, monkeypatch):
    captured: dict = {}

    class FakeHost:
        def __init__(self, status_item, **kwargs):
            captured.update(kwargs)

        def install(self):
            captured["installed"] = True

    monkeypatch.setattr(app, "_nsapp", SimpleNamespace(nsstatusitem=object()), raising=False)
    monkeypatch.setattr(menubar, "PanelHost", FakeHost)

    app._install_panel(None)

    assert captured["installed"] is True
    assert captured["get_seconds_since_check"]() is None or app._last_checked_at is not None
    status = _status("late-arrival")
    app.statuses = {status.repo_path: status}
    assert [tile.name for tile in captured["get_model"]().tiles] == ["late-arrival"]


def test_refresh_panel_failure_is_logged_as_a_panel_failure(app, logged):
    app._panel_host = MagicMock()
    app._panel_host.refresh.side_effect = RuntimeError("rebuild bug")

    app._refresh_panel()

    assert any("dashboard panel refresh raised" in line for line in logged)
    assert not any("check_all" in line for line in logged)


# -- PanelHost.install --------------------------------------------------------


def _host(status_item):
    return panel_host.PanelHost(
        status_item,
        get_model=lambda: None,
        get_seconds_since_check=lambda: None,
        on_repo_click=lambda path: None,
        on_stale_click=lambda path: None,
        on_refresh=lambda: None,
        on_add_repo=lambda: None,
    )


def test_install_failure_puts_the_menu_back(monkeypatch):
    menu = object()
    status_item = MagicMock()
    status_item.menu.return_value = menu
    status_item.setMenu_.side_effect = [RuntimeError("detach failed"), None]
    host = _host(status_item)

    with pytest.raises(RuntimeError):
        host.install()

    assert status_item.setMenu_.call_args_list[-1].args == (menu,)


def test_install_detaches_the_menu_only_after_the_click_handler_is_wired():
    status_item = MagicMock()
    order: list[str] = []
    status_item.button.return_value.setAction_.side_effect = lambda *_: order.append("action")
    status_item.setMenu_.side_effect = lambda *_: order.append("detach")

    _host(status_item).install()

    assert order == ["action", "detach"]

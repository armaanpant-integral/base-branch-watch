"""Exercises BaseBranchWatchApp's render + click-handler porcelain against
real rumps.MenuItem/Menu objects (WR-05) — no running NSApplication needed;
rumps.App/.MenuItem/.Timer construction is pure-Python/PyObjC object setup,
never touching the run loop until .run() is called.

Covers exactly the blind spot CR-01 and CR-02 lived in: _render_row never
wiring spec.children into a real submenu, and _repo_click_handler showing
"Up to date." for a CHECK_FAILED/DIVERGED base.
"""

from __future__ import annotations

import pytest
import rumps

from base_branch_watch.app import menubar
from base_branch_watch.core.models import BranchStatus, RepoConfig, RepoStatus, StatusKind


def _configure(app, status: RepoStatus) -> None:
    """_render's has_repos check reads app.cfg.repos, not the statuses list
    passed in — keep them in sync for these direct _render() calls."""
    bases = [bs.base for bs in status.branch_statuses]
    app.cfg.repos = [RepoConfig(repo_path=status.repo_path, base_branches=bases)]


def _capture_alert(monkeypatch) -> dict:
    """Monkeypatch rumps.alert to capture (title, message) instead of
    popping a real dialog; returns the dict alerts get recorded into."""
    captured: dict = {}

    def fake_alert(title, message):
        captured["title"] = title
        captured["message"] = message

    monkeypatch.setattr(rumps, "alert", fake_alert)
    return captured


def _status(name="repo-a", repo_path=None, unpushed=0, branch_statuses=None, failure_reason=None):
    return RepoStatus(
        repo_path=repo_path or f"/tmp/{name}",
        name=name,
        current_branch="main",
        unpushed=unpushed,
        branch_statuses=branch_statuses if branch_statuses is not None else [],
        failure_reason=failure_reason,
    )


def _multi_base_status(name="repo-multi", unpushed=0):
    return _status(
        name,
        unpushed=unpushed,
        branch_statuses=[
            BranchStatus(base="main", behind=0, ahead_of_base=0, kind=StatusKind.UP_TO_DATE),
            BranchStatus(base="release", behind=4, ahead_of_base=1, kind=StatusKind.DIVERGED),
        ],
    )


def _check_failed_single_base_status(name="repo-h", reason="fetch failed — check network/SSH"):
    return _status(
        name,
        branch_statuses=[
            BranchStatus(
                base="main", behind=0, ahead_of_base=0, kind=StatusKind.CHECK_FAILED, reason=reason
            )
        ],
    )


def _diverged_status(name="repo-g", behind=3, ahead=2):
    return _status(
        name,
        branch_statuses=[
            BranchStatus(base="main", behind=behind, ahead_of_base=ahead, kind=StatusKind.DIVERGED)
        ],
    )


@pytest.fixture()
def app(bbw_config_dir):
    """A real BaseBranchWatchApp — __init__ only does pure-Python/PyObjC
    object setup (Menu()/MenuItem()/Timer() never touch the NSApplication
    run loop until .run() is called), and cfg.repos is empty (isolated,
    fresh BBW_CONFIG_DIR), so check_all(None) inside __init__ is a real
    but instant no-op batch.check_all([]) — no git/subprocess calls."""
    instance = menubar.BaseBranchWatchApp()
    yield instance
    instance.timer.stop()


def test_render_multi_base_repo_builds_real_submenu_not_flat_row(app):
    status = _multi_base_status("myrepo")
    _configure(app, status)

    app._render([status])

    item = app._repo_items[status.repo_path]
    assert item.callback is None, "submenu parent must have no callback (Pitfall)"
    assert len(item) == 2, "parent should contain 2 real child MenuItems, not be a flat row"

    child_items = app._repo_child_items[status.repo_path]
    assert set(child_items.keys()) == {"main", "release"}
    for child in child_items.values():
        assert child.callback is not None, "each per-base child row should be clickable"

    assert child_items["main"].title == "🟢 main"
    assert child_items["release"].title == "🔴 release: diverged — 4 behind, 1 ahead"


def test_render_multi_base_repo_mutates_children_in_place_on_second_render(app):
    status = _multi_base_status("myrepo")
    _configure(app, status)
    app._render([status])
    item_first = app._repo_items[status.repo_path]
    child_first = app._repo_child_items[status.repo_path]["release"]

    updated = _multi_base_status("myrepo")
    updated.branch_statuses[1] = BranchStatus(
        base="release", behind=1, ahead_of_base=0, kind=StatusKind.BEHIND
    )
    app._render([updated])

    assert app._repo_items[status.repo_path] is item_first, "must mutate in place (Pitfall 10)"
    assert app._repo_child_items[status.repo_path]["release"] is child_first
    assert child_first.title == "🟡 release: 1 behind"


def test_render_single_base_repo_still_flat_and_clickable(app):
    status = _status(
        "myrepo",
        branch_statuses=[
            BranchStatus(base="main", behind=0, ahead_of_base=0, kind=StatusKind.UP_TO_DATE)
        ],
    )
    _configure(app, status)

    app._render([status])

    item = app._repo_items[status.repo_path]
    assert item.callback is not None
    assert len(item) == 0
    assert status.repo_path not in app._repo_child_items


def test_repo_click_handler_check_failed_base_shows_reason_not_up_to_date(app, monkeypatch):
    reason = "fetch failed — check network/SSH access"
    status = _check_failed_single_base_status("myrepo", reason=reason)
    app.statuses = {status.repo_path: status}
    captured = _capture_alert(monkeypatch)

    app._repo_click_handler(status.repo_path)(None)

    assert captured["message"] == reason
    assert captured["message"] != "Up to date."


def test_repo_click_handler_diverged_base_shows_diverged_detail(app, monkeypatch):
    status = _diverged_status("myrepo", behind=3, ahead=2)
    app.statuses = {status.repo_path: status}
    captured = _capture_alert(monkeypatch)

    app._repo_click_handler(status.repo_path)(None)

    assert "Diverged" in captured["message"]
    assert "3 behind" in captured["message"]
    assert "2 ahead" in captured["message"]


def test_repo_click_handler_multi_base_uses_worst_branch_not_first(app, monkeypatch):
    """CR-02 regression: branch_statuses[0] ('main', UP_TO_DATE) must not
    win over the worst base ('release', DIVERGED)."""
    status = _multi_base_status("myrepo")
    app.statuses = {status.repo_path: status}
    captured = _capture_alert(monkeypatch)

    app._repo_click_handler(status.repo_path)(None)

    assert "Diverged" in captured["message"]
    assert captured["message"] != "Up to date."


def test_edit_base_branches_replaces_existing_bases_without_duplicate_entry(app, monkeypatch):
    app.cfg.repos = [RepoConfig(repo_path="/tmp/myrepo", base_branches=["main"])]

    class FakeResp:
        clicked = True
        text = "main, release"

    fake_window = type("W", (), {"run": lambda self: FakeResp()})
    monkeypatch.setattr(rumps, "Window", lambda **kwargs: fake_window())

    app._edit_base_branches_click_handler("/tmp/myrepo")(None)

    assert len(app.cfg.repos) == 1, "must replace, not duplicate, the repo entry"
    assert app.cfg.repos[0].base_branches == ["main", "release"]


def test_edit_base_branches_cancel_leaves_config_untouched(app, monkeypatch):
    app.cfg.repos = [RepoConfig(repo_path="/tmp/myrepo", base_branches=["main"])]

    class FakeResp:
        clicked = False
        text = ""

    fake_window = type("W", (), {"run": lambda self: FakeResp()})
    monkeypatch.setattr(rumps, "Window", lambda **kwargs: fake_window())

    app._edit_base_branches_click_handler("/tmp/myrepo")(None)

    assert app.cfg.repos[0].base_branches == ["main"]


def test_child_click_handler_reports_specific_base_status(app, monkeypatch):
    status = _multi_base_status("myrepo")
    app.statuses = {status.repo_path: status}
    captured = _capture_alert(monkeypatch)

    app._child_click_handler(status.repo_path, "main")(None)
    assert captured["message"] == "Up to date."

    app._child_click_handler(status.repo_path, "release")(None)
    assert "Diverged" in captured["message"]
    assert "4 behind" in captured["message"]
    assert "1 ahead" in captured["message"]

"""Exercises BaseBranchWatchApp's render + click-handler porcelain against
real rumps.MenuItem/Menu objects (WR-05) — no running NSApplication needed;
rumps.App/.MenuItem/.Timer construction is pure-Python/PyObjC object setup,
never touching the run loop until .run() is called.

Covers exactly the blind spot CR-01 and CR-02 lived in: _render_row never
wiring spec.children into a real submenu, and _repo_click_handler showing
"Up to date." for a CHECK_FAILED/DIVERGED base.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
import rumps

from base_branch_watch.app import menubar
from base_branch_watch.core.models import (
    BranchStatus,
    PrStatus,
    PrStatusKind,
    RepoConfig,
    RepoStatus,
    StatusKind,
)


def _configure(app, status: RepoStatus) -> None:
    """_render's has_repos check reads app.cfg.repos, not the statuses list
    passed in — keep them in sync for these direct _render() calls."""
    bases = [bs.base for bs in status.branch_statuses]
    app.cfg.repos = [RepoConfig(repo_path=status.repo_path, base_branches=bases)]


def _capture_alert(monkeypatch) -> dict:
    """Monkeypatch BaseBranchWatchApp._show_alert (not rumps.alert — the app
    builds NSAlert directly now, see menubar._show_alert) to capture
    (title, message) instead of popping a real, blocking dialog."""
    captured: dict = {}

    def fake_show_alert(title, message, ok="OK", cancel=None):
        captured["title"] = title
        captured["message"] = message
        return 1

    monkeypatch.setattr(menubar.BaseBranchWatchApp, "_show_alert", staticmethod(fake_show_alert))
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


def _conflict_risk_single_base_status(name="repo-conflict", paths=("a.py", "b.py")):
    return _status(
        name,
        branch_statuses=[
            BranchStatus(
                base="main",
                behind=1,
                ahead_of_base=0,
                kind=StatusKind.CONFLICT_RISK,
                conflict_paths=list(paths),
            )
        ],
    )


def _conflict_risk_multi_base_status(name="repo-conflict-multi", paths=("a.py", "b.py")):
    return _status(
        name,
        branch_statuses=[
            BranchStatus(base="main", behind=0, ahead_of_base=0, kind=StatusKind.UP_TO_DATE),
            BranchStatus(
                base="release",
                behind=1,
                ahead_of_base=0,
                kind=StatusKind.CONFLICT_RISK,
                conflict_paths=list(paths),
            ),
        ],
    )


def test_render_conflict_risk_single_base_repo_does_not_raise(app):
    """Regression for the Phase 2 verification gap: a single-base
    CONFLICT_RISK row's own children are conflict-path leaves with
    callback_key=None — _render must not crash on that shape."""
    status = _conflict_risk_single_base_status()
    _configure(app, status)

    app._render([status])

    item = app._repo_items[status.repo_path]
    assert item.callback is None, "conflict-risk row becomes a submenu parent, no callback"
    assert len(item) == 2
    child_items = app._repo_child_items[status.repo_path]
    assert set(child_items.keys()) == {"a.py", "b.py"}
    for child in child_items.values():
        assert child.callback is None, "file-path rows are informational, never clickable"
    assert child_items["a.py"].title == "a.py"
    assert child_items["b.py"].title == "b.py"


def test_render_conflict_risk_multi_base_repo_nests_path_children(app):
    """A CONFLICT_RISK base inside a multi-base repo's submenu gets its own
    nested submenu of overlapping file paths, while its sibling non-conflict
    base stays a flat clickable child."""
    status = _conflict_risk_multi_base_status()
    _configure(app, status)

    app._render([status])

    parent = app._repo_items[status.repo_path]
    assert parent.callback is None
    child_items = app._repo_child_items[status.repo_path]
    # "main" keeps its stable base-derived key (has a callback_key); the
    # CONFLICT_RISK "release" row has no callback_key of its own, so it
    # falls back to a title-derived key (see _child_key).
    assert set(child_items.keys()) == {
        "main",
        "⚠️ release: conflict risk — 2 file(s) overlap",
    }

    main_child = child_items["main"]
    assert main_child.callback is not None
    assert len(main_child) == 0

    release_child = child_items["⚠️ release: conflict risk — 2 file(s) overlap"]
    assert release_child.callback is None, "conflict-risk base child has no callback of its own"
    assert len(release_child) == 2
    assert {item.title for item in release_child.values()} == {"a.py", "b.py"}


def test_render_conflict_risk_updates_nested_children_in_place_on_second_render(app):
    status = _conflict_risk_single_base_status(paths=("a.py",))
    _configure(app, status)
    app._render([status])
    item_first = app._repo_items[status.repo_path]

    updated = _conflict_risk_single_base_status(paths=("a.py", "c.py"))
    app._render([updated])

    assert app._repo_items[status.repo_path] is item_first, "must mutate in place (Pitfall 10)"
    child_items = app._repo_child_items[status.repo_path]
    assert set(child_items.keys()) == {"a.py", "c.py"}


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


# -- PR row (D-05/D-06/D-07/D-02/D-12) ----------------------------------------


def test_render_pr_row_open_builds_submenu_placed_after_git_row(app):
    status = _status(
        "myrepo",
        branch_statuses=[
            BranchStatus(base="main", behind=0, ahead_of_base=0, kind=StatusKind.UP_TO_DATE)
        ],
    )
    _configure(app, status)
    app._render([status])

    pr_status = PrStatus(
        kind=PrStatusKind.OPEN,
        number=42,
        checks_pass=3,
        checks_total=3,
        review_decision="APPROVED",
        merge_state_status="CLEAN",
    )
    from base_branch_watch.app import menu_builder

    spec = menu_builder._pr_row(pr_status, status.name)
    app._render_pr_row(status.repo_path, spec)

    pr_item = app._pr_items[status.repo_path]
    assert pr_item.callback is None
    assert len(pr_item) == 3

    menu_keys = list(app.menu.keys())
    git_key = app._repo_item_keys[status.repo_path]
    pr_key = app._pr_item_keys[status.repo_path]
    assert menu_keys.index(pr_key) == menu_keys.index(git_key) + 1, (
        "PR row must sit immediately after its repo's git-status row"
    )


def test_render_pr_row_mutates_children_in_place_on_second_render(app):
    status = _status(
        "myrepo",
        branch_statuses=[
            BranchStatus(base="main", behind=0, ahead_of_base=0, kind=StatusKind.UP_TO_DATE)
        ],
    )
    _configure(app, status)
    app._render([status])

    from base_branch_watch.app import menu_builder

    open_status = PrStatus(
        kind=PrStatusKind.OPEN,
        number=42,
        checks_pass=2,
        checks_fail=1,
        checks_total=3,
        review_decision="REVIEW_REQUIRED",
        merge_state_status="DIRTY",
    )
    spec_first = menu_builder._pr_row(open_status, status.name)
    app._render_pr_row(status.repo_path, spec_first)
    pr_item_first = app._pr_items[status.repo_path]
    checks_child_first = app._pr_child_items[status.repo_path][0]

    updated_status = PrStatus(
        kind=PrStatusKind.OPEN,
        number=42,
        checks_pass=3,
        checks_total=3,
        review_decision="APPROVED",
        merge_state_status="CLEAN",
    )
    spec_second = menu_builder._pr_row(updated_status, status.name)
    app._render_pr_row(status.repo_path, spec_second)

    assert app._pr_items[status.repo_path] is pr_item_first, "must mutate in place (Pitfall 10)"
    assert app._pr_child_items[status.repo_path][0] is checks_child_first
    assert checks_child_first.title == "✅ Checks: 3/3 passing"
    assert pr_item_first.title == spec_second.title


def test_render_pr_row_no_pr_flat_row_no_children(app):
    status = _status(
        "myrepo",
        branch_statuses=[
            BranchStatus(base="main", behind=0, ahead_of_base=0, kind=StatusKind.UP_TO_DATE)
        ],
    )
    _configure(app, status)
    app._render([status])

    from base_branch_watch.app import menu_builder

    pr_status = PrStatus(kind=PrStatusKind.NO_PR, current_branch="main")
    spec = menu_builder._pr_row(pr_status, status.name)
    app._render_pr_row(status.repo_path, spec)

    pr_item = app._pr_items[status.repo_path]
    assert pr_item.title == "⚪ myrepo: no open PR (main)"
    assert len(pr_item) == 0
    assert status.repo_path not in app._pr_child_items


def test_check_all_renders_pr_row_from_fake_batch_result_without_raising(app, monkeypatch):
    """A fake batch.check_all result (RepoStatus + PrStatus per repo) must
    render a PR row into self._pr_items per repo without raising — the exact
    integration point the plan calls out."""
    status = _status(
        "myrepo",
        branch_statuses=[
            BranchStatus(base="main", behind=0, ahead_of_base=0, kind=StatusKind.UP_TO_DATE)
        ],
    )
    app.cfg.repos = [RepoConfig(repo_path=status.repo_path, base_branches=["main"])]
    pr_status = PrStatus(kind=PrStatusKind.NO_PR, current_branch="main")

    with patch(
        "base_branch_watch.app.menubar.batch.check_all",
        return_value=([status], {status.repo_path: pr_status}),
    ):
        app.check_all(None)

    assert status.repo_path in app._pr_items
    assert app._pr_items[status.repo_path].title == "⚪ myrepo: no open PR (main)"
    assert app._pr_statuses[status.repo_path] is pr_status

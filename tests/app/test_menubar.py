"""Exercises BaseBranchWatchApp's render + click-handler porcelain against
real rumps.MenuItem/Menu objects (WR-05) — no running NSApplication needed;
rumps.App/.MenuItem/.Timer construction is pure-Python/PyObjC object setup,
never touching the run loop until .run() is called.

Covers exactly the blind spot CR-01 and CR-02 lived in: _render_row never
wiring spec.children into a real submenu, and _repo_click_handler showing
"Up to date." for a CHECK_FAILED/DIVERGED base.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from base_branch_watch.app import menubar
from base_branch_watch.core.models import (
    BranchStatus,
    PrStatus,
    PrStatusKind,
    RepoConfig,
    RepoStatus,
    StaleBranch,
    StaleKind,
    StatusKind,
)
from base_branch_watch.runner import poll_cycle


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
    assert child_items["release"].title == "🔴 release: diverged - 4 behind, 1 ahead"


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
        "⚠️ release: conflict risk - 2 file(s) overlap",
    }

    main_child = child_items["main"]
    assert main_child.callback is not None
    assert len(main_child) == 0

    release_child = child_items["⚠️ release: conflict risk - 2 file(s) overlap"]
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

    assert "diverged" in captured["message"]
    assert "3 behind" in captured["message"]
    assert "2 ahead" in captured["message"]


def test_repo_click_handler_multi_base_uses_worst_branch_not_first(app, monkeypatch):
    """CR-02 regression: branch_statuses[0] ('main', UP_TO_DATE) must not
    win over the worst base ('release', DIVERGED)."""
    status = _multi_base_status("myrepo")
    app.statuses = {status.repo_path: status}
    captured = _capture_alert(monkeypatch)

    app._repo_click_handler(status.repo_path)(None)

    assert "diverged" in captured["message"]
    assert captured["message"] != "Up to date."


def test_edit_base_branches_replaces_existing_bases_without_duplicate_entry(app, monkeypatch):
    app.cfg.repos = [RepoConfig(repo_path="/tmp/myrepo", base_branches=["main"])]

    class FakeResp:
        clicked = True
        text = "main, release"

    monkeypatch.setattr(
        menubar.BaseBranchWatchApp,
        "_show_text_prompt",
        staticmethod(lambda title, message, default_text, ok, cancel: FakeResp()),
    )

    app._edit_base_branches_click_handler("/tmp/myrepo")(None)

    assert len(app.cfg.repos) == 1, "must replace, not duplicate, the repo entry"
    assert app.cfg.repos[0].base_branches == ["main", "release"]


def test_edit_base_branches_cancel_leaves_config_untouched(app, monkeypatch):
    app.cfg.repos = [RepoConfig(repo_path="/tmp/myrepo", base_branches=["main"])]

    class FakeResp:
        clicked = False
        text = ""

    monkeypatch.setattr(
        menubar.BaseBranchWatchApp,
        "_show_text_prompt",
        staticmethod(lambda title, message, default_text, ok, cancel: FakeResp()),
    )

    app._edit_base_branches_click_handler("/tmp/myrepo")(None)

    assert app.cfg.repos[0].base_branches == ["main"]


def test_show_text_prompt_sets_can_join_all_spaces(monkeypatch):
    """_show_text_prompt must force the rumps.Window's backing NSAlert window
    to join the user's CURRENT macOS Space before running (mirrors
    _show_alert's fix), while still delegating the run to rumps.Window."""

    class FakeResponse:
        clicked = True
        text = "main"

    fake_window = MagicMock()
    fake_window.run.return_value = FakeResponse()
    monkeypatch.setattr(menubar.rumps, "Window", lambda **kwargs: fake_window)

    resp = menubar.BaseBranchWatchApp._show_text_prompt(
        title="t", message="m", default_text="d", ok="OK", cancel="Cancel"
    )

    fake_window._alert.window().setCollectionBehavior_.assert_called_once_with(
        menubar.NSWindowCollectionBehaviorCanJoinAllSpaces
    )
    fake_window.run.assert_called_once()
    assert resp is fake_window.run.return_value


def test_add_repo_open_panel_sets_can_join_all_spaces(app, monkeypatch):
    """_add_repo's NSOpenPanel folder picker must set collectionBehavior
    before runModal() so it opens on the user's CURRENT macOS Space.

    Patches the module-level `NSOpenPanel` name (not an attribute on the
    real PyObjC class) -- PyObjC classes don't support monkeypatch's
    setattr/delattr round-trip on their selectors, and replacing the real
    class's `openPanel` classmethod would invoke the actual native picker."""
    panel = MagicMock()
    panel.runModal.return_value = 0  # non-1 -> cancel branch, no further work
    fake_open_panel_cls = MagicMock()
    fake_open_panel_cls.openPanel.return_value = panel
    monkeypatch.setattr(menubar, "NSOpenPanel", fake_open_panel_cls)
    monkeypatch.setattr(app, "_activate", lambda: None)

    app._add_repo(None)

    panel.setCollectionBehavior_.assert_called_once_with(
        menubar.NSWindowCollectionBehaviorCanJoinAllSpaces
    )


def test_child_click_handler_reports_specific_base_status(app, monkeypatch):
    status = _multi_base_status("myrepo")
    app.statuses = {status.repo_path: status}
    captured = _capture_alert(monkeypatch)

    app._child_click_handler(status.repo_path, "main")(None)
    assert captured["message"] == "Up to date."

    app._child_click_handler(status.repo_path, "release")(None)
    assert "diverged" in captured["message"]
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


# -- Task 3 (Plan 02): ~2min PR-check floor + MERGED/CLOSED one-cycle
# transition (D-13/D-03) ------------------------------------------------------


def test_check_all_pr_floor_skips_second_call_within_2min_but_retains_row(app):
    status = _status(
        "myrepo",
        branch_statuses=[
            BranchStatus(base="main", behind=0, ahead_of_base=0, kind=StatusKind.UP_TO_DATE)
        ],
    )
    app.cfg.repos = [RepoConfig(repo_path=status.repo_path, base_branches=["main"])]
    pr_result = PrStatus(kind=PrStatusKind.NO_PR, current_branch="main")

    with (
        patch("base_branch_watch.runner.batch.base_verdict.check_repo", return_value=status),
        patch(
            "base_branch_watch.runner.batch.pr_status.check_pr", return_value=pr_result
        ) as mock_check_pr,
    ):
        app.check_all(None)
        app.check_all(None)

    assert mock_check_pr.call_count == 1, "second cycle within the 2min floor must not re-call gh"
    assert app._pr_items[status.repo_path].title == "⚪ myrepo: no open PR (main)"


def test_check_all_open_to_no_pr_transition_renders_merged_for_one_cycle_then_falls_back(app):
    status = _status(
        "myrepo",
        branch_statuses=[
            BranchStatus(base="main", behind=0, ahead_of_base=0, kind=StatusKind.UP_TO_DATE)
        ],
    )
    app.cfg.repos = [RepoConfig(repo_path=status.repo_path, base_branches=["main"])]

    open_status = PrStatus(
        kind=PrStatusKind.OPEN,
        number=7,
        checks_pass=3,
        checks_total=3,
        review_decision="APPROVED",
        merge_state_status="CLEAN",
    )
    no_pr_status = PrStatus(kind=PrStatusKind.NO_PR, current_branch="main")

    with patch.object(
        app._cycle,
        "_check_all",
        return_value=([status], {status.repo_path: open_status}),
    ):
        app.check_all(None)
    assert app._cycle.pr_statuses[status.repo_path].kind == PrStatusKind.OPEN

    # Force PR-check eligibility for the next cycle (bypass the 2min floor
    # in this fast-running test -- the floor itself is covered by the
    # dedicated floor test above).
    app._cycle.last_pr_checked_at[status.repo_path] = 0.0

    with (
        patch.object(
            app._cycle,
            "_check_all",
            return_value=([status], {status.repo_path: no_pr_status}),
        ),
        patch.object(app._cycle, "_final_state", return_value=PrStatusKind.MERGED),
    ):
        app.check_all(None)

    assert app._cycle.pr_statuses[status.repo_path].kind == PrStatusKind.MERGED
    assert app._cycle.pr_statuses[status.repo_path].number == 7
    assert app._pr_items[status.repo_path].title == "✅ myrepo: PR #7 merged"

    app._cycle.last_pr_checked_at[status.repo_path] = 0.0

    with patch.object(
        app._cycle,
        "_check_all",
        return_value=([status], {status.repo_path: no_pr_status}),
    ):
        app.check_all(None)

    assert app._cycle.pr_statuses[status.repo_path].kind == PrStatusKind.NO_PR
    assert app._pr_items[status.repo_path].title == "⚪ myrepo: no open PR (main)"


def test_check_all_merged_pr_still_found_by_gh_renders_once_then_collapses_to_no_pr(app):
    """CR-01/Route B regression: `gh pr view` keeps resolving to the same
    MERGED PR directly (branch not deleted) rather than falling through to
    NO_PR. The MERGED row must still show for exactly one cycle (D-03) --
    the SECOND cycle seeing the same terminal PR number must collapse to
    NO_PR rather than repeating "PR merged" indefinitely."""
    status = _status(
        "myrepo",
        branch_statuses=[
            BranchStatus(base="main", behind=0, ahead_of_base=0, kind=StatusKind.UP_TO_DATE)
        ],
    )
    app.cfg.repos = [RepoConfig(repo_path=status.repo_path, base_branches=["main"])]

    merged_status = PrStatus(kind=PrStatusKind.MERGED, number=7)

    with patch.object(
        app._cycle,
        "_check_all",
        return_value=([status], {status.repo_path: merged_status}),
    ):
        app.check_all(None)

    assert app._cycle.pr_statuses[status.repo_path].kind == PrStatusKind.MERGED
    assert app._pr_items[status.repo_path].title == "✅ myrepo: PR #7 merged"

    app._cycle.last_pr_checked_at[status.repo_path] = 0.0
    with (
        patch.object(
            app._cycle,
            "_check_all",
            # gh STILL reports the same merged PR directly -- branch never
            # disappeared, so batch.check_all never returns NO_PR here.
            return_value=(
                [status],
                {status.repo_path: PrStatus(kind=PrStatusKind.MERGED, number=7)},
            ),
        ),
        patch.object(app._cycle, "_current_branch", return_value="main"),
    ):
        app.check_all(None)

    assert app._cycle.pr_statuses[status.repo_path].kind == PrStatusKind.NO_PR
    assert app._pr_items[status.repo_path].title == "⚪ myrepo: no open PR (main)"


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

    with patch.object(
        app._cycle,
        "_check_all",
        return_value=([status], {status.repo_path: pr_status}),
    ):
        app.check_all(None)

    assert status.repo_path in app._pr_items
    assert app._pr_items[status.repo_path].title == "⚪ myrepo: no open PR (main)"
    assert app._cycle.pr_statuses[status.repo_path] is pr_status


def test_log_status_line_check_failed_base_is_labelled_fail():
    line = poll_cycle.status_log_line(_check_failed_single_base_status(name="repo-h"))

    assert line.startswith("[FAIL] repo-h: check failed - ")
    assert line.endswith("(main)")


def test_log_status_line_non_failed_bases_stay_ok():
    up_to_date = _status(
        "repo-u",
        branch_statuses=[
            BranchStatus(base="main", behind=0, ahead_of_base=0, kind=StatusKind.UP_TO_DATE)
        ],
    )

    assert poll_cycle.status_log_line(up_to_date) == "[OK] repo-u: up to date"
    assert poll_cycle.status_log_line(_diverged_status("repo-d")).startswith("[OK] repo-d: ")


# -- self-update check ------------------------------------------------------


def _update_info(behind=3, sha="remote-sha-1", subjects=None):
    return menubar.self_update.UpdateInfo(
        base="main",
        behind=behind,
        remote_sha=sha,
        subjects=subjects if subjects is not None else ["add thing", "fix other"],
    )


@pytest.fixture()
def update_app(app, monkeypatch):
    """The app with a (fake) source checkout and a captured notifier."""
    app._checkout_root = "/fake/checkout"
    app._last_update_checked_at = float("-inf")
    app._notifier = MagicMock()
    monkeypatch.setattr(menubar.state, "save_state", lambda s: None)
    return app


def test_update_found_adds_menu_row_and_notifies_once(update_app, monkeypatch):
    monkeypatch.setattr(menubar.self_update, "check_for_update", lambda root: _update_info())

    update_app._maybe_check_for_update()

    assert update_app._update_item is not None
    assert update_app._update_item.title == "⬆️ Update available (3 new commits)…"
    update_app._notifier.send_message.assert_called_once()
    title, body = update_app._notifier.send_message.call_args.args
    assert "3" in title or "3" in body


def test_same_remote_sha_does_not_notify_again(update_app, monkeypatch):
    monkeypatch.setattr(menubar.self_update, "check_for_update", lambda root: _update_info())

    update_app._maybe_check_for_update()
    update_app._last_update_checked_at = float("-inf")
    update_app._maybe_check_for_update()

    assert update_app._notifier.send_message.call_count == 1


def test_new_remote_sha_notifies_again(update_app, monkeypatch):
    infos = iter([_update_info(sha="sha-a"), _update_info(behind=4, sha="sha-b")])
    monkeypatch.setattr(menubar.self_update, "check_for_update", lambda root: next(infos))

    update_app._maybe_check_for_update()
    update_app._last_update_checked_at = float("-inf")
    update_app._maybe_check_for_update()

    assert update_app._notifier.send_message.call_count == 2
    assert update_app._update_item.title == "⬆️ Update available (4 new commits)…"


def test_up_to_date_removes_update_row(update_app, monkeypatch):
    results = iter([_update_info(), None])
    monkeypatch.setattr(menubar.self_update, "check_for_update", lambda root: next(results))

    update_app._maybe_check_for_update()
    update_app._last_update_checked_at = float("-inf")
    update_app._maybe_check_for_update()

    assert update_app._update_item is None


def test_update_check_runs_at_most_once_per_interval(update_app, monkeypatch):
    calls = []
    monkeypatch.setattr(
        menubar.self_update, "check_for_update", lambda root: calls.append(root) or None
    )

    update_app._maybe_check_for_update()
    update_app._maybe_check_for_update()

    assert calls == ["/fake/checkout"]


def test_no_update_check_without_source_checkout(update_app, monkeypatch):
    update_app._checkout_root = None
    monkeypatch.setattr(
        menubar.self_update,
        "check_for_update",
        lambda root: pytest.fail("must not check without a checkout"),
    )

    update_app._maybe_check_for_update()

    assert update_app._update_item is None


def test_clicking_update_row_shows_commits_and_update_steps(update_app, monkeypatch):
    captured = _capture_alert(monkeypatch)
    monkeypatch.setattr(menubar.self_update, "check_for_update", lambda root: _update_info())
    update_app._maybe_check_for_update()

    update_app._show_update_info(None)

    assert "add thing" in captured["message"]
    assert "fix other" in captured["message"]
    assert "git pull --ff-only" in captured["message"]
    assert "/fake/checkout" in captured["message"]
    assert "Quit" in captured["message"]


# -- notification gating through check_all -----------------------------------


def _gating_status(unpushed=0, sha="sha-1", kind=StatusKind.BEHIND):
    return _status(
        "myrepo",
        unpushed=unpushed,
        branch_statuses=[
            BranchStatus(base="main", behind=2, ahead_of_base=0, kind=kind, head_sha=sha)
        ],
    )


@pytest.fixture()
def gating_app(app, monkeypatch):
    """App whose polling cycle returns whatever `app.next_statuses` holds."""
    saves: list = []
    app._notifier = MagicMock()
    app.saves = saves
    app.next_statuses = []
    app.cfg.repos = [RepoConfig(repo_path="/tmp/myrepo", base_branches=["main"])]
    monkeypatch.setattr(menubar.state, "save_state", lambda s: saves.append(s))
    monkeypatch.setattr(
        app._cycle, "_check_all", lambda repos, pr_repo_paths=None: (app.next_statuses, {})
    )
    return app


def test_check_all_digest_dedupes_unchanged_sha_across_cycles(gating_app):
    gating_app.next_statuses = [_gating_status()]

    gating_app.check_all(None)
    gating_app.check_all(None)

    assert gating_app._notifier.send_digest.call_count == 1


def test_check_all_persists_cleared_unpushed_count_even_when_nothing_notifies(gating_app):
    gating_app.next_statuses = [_gating_status(unpushed=3, kind=StatusKind.UNPUSHED, sha="s")]
    gating_app.check_all(None)
    saves_after_first = len(gating_app.saves)

    # Pushed: unpushed drops to 0, repo is OK -> no digest, but the stored count
    # must still be cleared and saved or the next identical count is swallowed.
    gating_app.next_statuses = [_gating_status(unpushed=0, kind=StatusKind.UP_TO_DATE, sha="s")]
    gating_app.check_all(None)

    assert gating_app._notifier.send_digest.call_count == 1
    assert len(gating_app.saves) == saves_after_first + 1
    assert menubar.notify_policy.UNPUSHED_KEY not in gating_app._state.get("/tmp/myrepo", {})


def test_check_all_failed_digest_send_does_not_mark_state_notified(gating_app):
    gating_app.next_statuses = [_gating_status()]
    gating_app._notifier.send_digest.side_effect = [RuntimeError("osascript down"), None]

    gating_app.check_all(None)
    gating_app.check_all(None)

    assert gating_app._notifier.send_digest.call_count == 2


def test_repo_click_handler_conflict_risk_names_the_conflict(app, monkeypatch):
    status = _conflict_risk_single_base_status("myrepo", paths=("a.py", "b.py"))
    app.statuses = {status.repo_path: status}
    captured = _capture_alert(monkeypatch)

    app._repo_click_handler(status.repo_path)(None)

    assert captured["message"] == "conflict risk - 2 file(s) overlap (main)"


def test_log_status_line_conflict_risk_names_the_conflict():
    status = _conflict_risk_single_base_status("repo-c", paths=("a.py",))

    assert (
        poll_cycle.status_log_line(status)
        == "[OK] repo-c: conflict risk - 1 file(s) overlap (main)"
    )


def test_repo_click_handler_up_to_date_with_unpushed_shows_the_unpushed_count(app, monkeypatch):
    status = _status(
        "myrepo",
        unpushed=2,
        branch_statuses=[
            BranchStatus(base="main", behind=0, ahead_of_base=0, kind=StatusKind.UP_TO_DATE)
        ],
    )
    app.statuses = {status.repo_path: status}
    captured = _capture_alert(monkeypatch)

    app._repo_click_handler(status.repo_path)(None)

    assert captured["message"] == "2 unpushed"


def test_check_all_renders_a_stale_branch_row_with_a_child_per_branch(app):
    status = _status(
        "myrepo",
        branch_statuses=[
            BranchStatus(base="main", behind=0, ahead_of_base=0, kind=StatusKind.UP_TO_DATE)
        ],
    )
    app.cfg.repos = [RepoConfig(repo_path=status.repo_path, base_branches=["main"])]
    stale = {status.repo_path: [StaleBranch(name="feat/x", kind=StaleKind.GONE_MERGED)]}

    with (
        patch.object(app._cycle, "_check_all", return_value=([status], {})),
        patch.object(app._cycle, "_check_stale", return_value=stale),
    ):
        app.check_all(None)

    item = app._stale_items[status.repo_path]
    assert item.title == "🧹 myrepo: 1 stale branch"
    assert [child.title for child in item.values()] == ["feat/x - remote deleted, merged"]


def _run_cycle_with_stale(app, status, stale):
    with (
        patch.object(app._cycle, "_check_all", return_value=([status], {})),
        patch.object(app._cycle, "_check_stale", return_value=stale),
    ):
        app.check_all(None)


def _up_to_date(name: str) -> RepoStatus:
    return _status(
        name,
        branch_statuses=[
            BranchStatus(base="main", behind=0, ahead_of_base=0, kind=StatusKind.UP_TO_DATE)
        ],
    )


def test_stale_row_disappears_when_the_next_cycle_cannot_read_origin(app):
    status = _up_to_date("myrepo")
    app.cfg.repos = [RepoConfig(repo_path=status.repo_path, base_branches=["main"])]
    one_stale = {status.repo_path: [StaleBranch(name="feat/x", kind=StaleKind.GONE_MERGED)]}
    _run_cycle_with_stale(app, status, one_stale)
    title = app._stale_items[status.repo_path].title

    _run_cycle_with_stale(app, status, {})  # repo missing = origin unreadable this cycle

    assert status.repo_path not in app._stale_items
    assert title not in app.menu


def test_stale_row_is_removed_when_its_repo_is_no_longer_watched(app):
    kept, dropped = _up_to_date("kept"), _up_to_date("dropped")
    app.cfg.repos = [
        RepoConfig(repo_path=kept.repo_path, base_branches=["main"]),
        RepoConfig(repo_path=dropped.repo_path, base_branches=["main"]),
    ]
    stale = {
        kept.repo_path: [StaleBranch(name="a", kind=StaleKind.GONE_MERGED)],
        dropped.repo_path: [StaleBranch(name="b", kind=StaleKind.GONE_MERGED)],
    }
    with (
        patch.object(app._cycle, "_check_all", return_value=([kept, dropped], {})),
        patch.object(app._cycle, "_check_stale", return_value=stale),
    ):
        app.check_all(None)
    dropped_title = app._stale_items[dropped.repo_path].title

    app.cfg.repos = [RepoConfig(repo_path=kept.repo_path, base_branches=["main"])]
    app._render([kept])

    assert dropped.repo_path not in app._stale_items
    assert dropped_title not in app.menu
    assert kept.repo_path in app._stale_items

    app.cfg.repos = []
    app._render([])

    assert app._stale_items == {}


def test_stale_row_can_be_removed_after_its_title_changed_with_the_count(app):
    status = _up_to_date("myrepo")
    app.cfg.repos = [RepoConfig(repo_path=status.repo_path, base_branches=["main"])]
    two = {
        status.repo_path: [
            StaleBranch(name="a", kind=StaleKind.GONE_MERGED),
            StaleBranch(name="b", kind=StaleKind.MERGED),
        ]
    }
    one = {status.repo_path: [StaleBranch(name="a", kind=StaleKind.GONE_MERGED)]}
    _run_cycle_with_stale(app, status, two)
    _run_cycle_with_stale(app, status, one)
    assert app._stale_items[status.repo_path].title == "🧹 myrepo: 1 stale branch"

    _run_cycle_with_stale(app, status, {status.repo_path: []})

    assert status.repo_path not in app._stale_items
    assert "🧹 myrepo: 2 stale branches" not in app.menu
    assert "🧹 myrepo: 1 stale branch" not in app.menu


def test_removing_a_stale_row_the_menu_already_lost_does_not_raise_or_leave_tracking(app):
    status = _up_to_date("myrepo")
    app.cfg.repos = [RepoConfig(repo_path=status.repo_path, base_branches=["main"])]
    one = {status.repo_path: [StaleBranch(name="a", kind=StaleKind.GONE_MERGED)]}
    _run_cycle_with_stale(app, status, one)
    del app.menu[app._stale_item_keys[status.repo_path]]

    app._remove_stale_row([status.repo_path])

    assert status.repo_path not in app._stale_items
    assert status.repo_path not in app._stale_item_keys


def test_failed_config_save_is_shown_to_the_user(app, monkeypatch):
    from base_branch_watch.core import config
    from base_branch_watch.core.store import WriteResult

    monkeypatch.setattr(
        config, "save_config", lambda cfg: WriteResult(ok=False, error="No space left on device")
    )
    captured = _capture_alert(monkeypatch)

    app._save_config()

    assert captured["title"] == "Could not save config"
    assert captured["message"] == "No space left on device"


def test_successful_config_save_shows_no_alert(app, monkeypatch):
    captured = _capture_alert(monkeypatch)

    app._save_config()

    assert captured == {}


def test_startup_quarantines_a_corrupt_config_instead_of_overwriting_it(bbw_config_dir):
    bbw_config_dir.mkdir(parents=True, exist_ok=True)
    (bbw_config_dir / "config.json").write_text("{not json")

    instance = menubar.BaseBranchWatchApp()
    try:
        assert instance.cfg.repos == []
    finally:
        instance.timer.stop()

    assert (bbw_config_dir / "config.json.corrupt").read_text() == "{not json"

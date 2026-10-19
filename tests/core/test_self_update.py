"""Tests for core.self_update -- is the app's own checkout behind origin?"""

from __future__ import annotations

from pathlib import Path

from base_branch_watch.core import self_update, state
from tests.conftest import _run


def test_check_for_update_reports_commits_behind_origin(fixture_repos, default_branch_name):
    _origin, clone_path = fixture_repos

    info = self_update.check_for_update(clone_path)

    assert info is not None
    assert info.base == default_branch_name
    assert info.behind == 1
    assert info.subjects == ["second commit"]
    assert (
        info.remote_sha
        == _run(["rev-parse", f"origin/{default_branch_name}"], Path(clone_path)).stdout.strip()
    )


def test_check_for_update_none_when_up_to_date(fixture_repos_up_to_date):
    _origin, clone_path = fixture_repos_up_to_date

    assert self_update.check_for_update(clone_path) is None


def test_check_for_update_none_when_checkout_not_on_default_branch(
    fixture_repos, default_branch_name
):
    # A dev checkout sitting on a feature branch must not be nagged to "update".
    _origin, clone_path = fixture_repos
    _run(["checkout", "-b", "feature/wip"], Path(clone_path))

    assert self_update.check_for_update(clone_path) is None


def test_check_for_update_none_when_fetch_fails(fixture_repos_fetch_fails):
    _origin, clone_path = fixture_repos_fetch_fails

    assert self_update.check_for_update(clone_path) is None


def test_check_for_update_caps_subjects_but_reports_full_behind_count(
    fixture_repos, default_branch_name
):
    origin_path, clone_path = fixture_repos
    for n in range(7):
        (Path(origin_path) / "file.txt").write_text(f"v{n}\n")
        _run(["commit", "-am", f"extra {n}"], Path(origin_path))

    info = self_update.check_for_update(clone_path)

    assert info is not None
    assert info.behind == 8
    assert len(info.subjects) == self_update.MAX_SUBJECTS


def test_checkout_root_none_without_git_dir(tmp_path):
    assert (
        self_update.checkout_root(
            tmp_path / "site-packages" / "base_branch_watch" / "core" / "x.py"
        )
        is None
    )


def test_checkout_root_found_for_editable_checkout(tmp_path):
    root = tmp_path / "repo"
    module_file = root / "base_branch_watch" / "core" / "self_update.py"
    module_file.parent.mkdir(parents=True)
    (root / ".git").mkdir()

    assert self_update.checkout_root(module_file) == str(root)


def test_update_notification_dedupes_on_remote_sha():
    s: state.State = {}
    assert state.should_notify_update(s, "sha1") is True
    s = state.mark_notified_update(s, "sha1")
    assert state.should_notify_update(s, "sha1") is False
    assert state.should_notify_update(s, "sha2") is True

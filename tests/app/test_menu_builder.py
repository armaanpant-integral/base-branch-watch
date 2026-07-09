from __future__ import annotations

from base_branch_watch.app import menu_builder
from base_branch_watch.core.models import BranchStatus, RepoStatus, StatusKind


def _up_to_date_status(name="repo-a"):
    return RepoStatus(
        repo_path=f"/tmp/{name}",
        name=name,
        current_branch="main",
        unpushed=0,
        branch_statuses=[
            BranchStatus(base="main", behind=0, ahead_of_base=0, kind=StatusKind.UP_TO_DATE)
        ],
    )


def _behind_status(name="repo-b", behind=3, base="main"):
    return RepoStatus(
        repo_path=f"/tmp/{name}",
        name=name,
        current_branch="feature",
        unpushed=0,
        branch_statuses=[
            BranchStatus(base=base, behind=behind, ahead_of_base=0, kind=StatusKind.BEHIND)
        ],
    )


def _not_checked_status(name="repo-c"):
    return RepoStatus(
        repo_path=f"/tmp/{name}",
        name=name,
        current_branch=None,
        unpushed=0,
        branch_statuses=[],
    )


def _check_failed_status(name="repo-d", reason="fetch failed — check network/SSH access"):
    return RepoStatus(
        repo_path=f"/tmp/{name}",
        name=name,
        current_branch=None,
        unpushed=0,
        branch_statuses=[],
        failure_reason=reason,
    )


def test_build_empty_state_when_no_repos():
    specs = menu_builder.build([], has_repos=False)

    assert len(specs) == 1
    assert specs[0].title == "No repos watched — click Add Repo… below"
    assert specs[0].callback_key is None


def test_build_up_to_date_row():
    specs = menu_builder.build([_up_to_date_status("myrepo")], has_repos=True)

    assert specs[0].title == "🟢 myrepo"


def test_build_behind_row_is_clickable():
    status = _behind_status("myrepo", behind=3, base="main")
    specs = menu_builder.build([status], has_repos=True)

    assert specs[0].title == "🟡 myrepo: 3 behind (main)"
    assert specs[0].callback_key is not None


def test_build_not_checked_row_uses_ellipsis_and_is_not_clickable():
    specs = menu_builder.build([_not_checked_status("myrepo")], has_repos=True)

    assert specs[0].title == "… myrepo"
    assert specs[0].callback_key is None


def test_title_for_up_to_date_only():
    assert menu_builder.title_for([_up_to_date_status()]) == "🟢"


def test_title_for_behind():
    assert menu_builder.title_for([_behind_status()]) == "🟡 1"


def test_title_for_check_failed():
    assert menu_builder.title_for([_check_failed_status()]) == "🔴 1"

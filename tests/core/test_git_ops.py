from __future__ import annotations

from base_branch_watch.core import git_ops
from base_branch_watch.core.models import RepoConfig, StatusKind


def test_check_repo_behind_reports_behind_count_and_kind(fixture_repos, default_branch_name):
    _origin, clone_path = fixture_repos

    status = git_ops.check_repo(RepoConfig(repo_path=clone_path, base_branches=[default_branch_name]))

    assert status.failure_reason is None
    assert len(status.branch_statuses) == 1
    branch_status = status.branch_statuses[0]
    assert branch_status.behind > 0
    assert branch_status.kind == StatusKind.BEHIND
    assert status.worst_kind == StatusKind.BEHIND


def test_check_repo_up_to_date(fixture_repos_up_to_date, default_branch_name):
    _origin, clone_path = fixture_repos_up_to_date

    status = git_ops.check_repo(
        RepoConfig(repo_path=clone_path, base_branches=[default_branch_name])
    )

    assert status.failure_reason is None
    assert status.worst_kind == StatusKind.UP_TO_DATE
    assert status.branch_statuses[0].behind == 0


def test_check_repo_nonexistent_path_never_raises(tmp_path):
    missing = tmp_path / "does-not-exist"

    status = git_ops.check_repo(RepoConfig(repo_path=str(missing), base_branches=["main"]))

    assert status.failure_reason is not None
    assert status.worst_kind == StatusKind.CHECK_FAILED


def test_detect_default_branch_returns_fixture_default(fixture_repos, default_branch_name):
    origin_path, clone_path = fixture_repos

    detected = git_ops.detect_default_branch(clone_path)

    assert detected == default_branch_name

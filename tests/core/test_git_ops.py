from __future__ import annotations

from base_branch_watch.core import git_ops
from base_branch_watch.core.models import RepoConfig, Severity, StatusKind


def test_check_repo_behind_reports_behind_count_and_kind(fixture_repos, default_branch_name):
    _origin, clone_path = fixture_repos

    status = git_ops.check_repo(
        RepoConfig(repo_path=clone_path, base_branches=[default_branch_name])
    )

    assert status.failure_reason is None
    assert len(status.branch_statuses) == 1
    branch_status = status.branch_statuses[0]
    assert branch_status.behind > 0
    assert branch_status.ahead_of_base == 0
    assert branch_status.kind == StatusKind.BEHIND
    assert status.worst_kind == StatusKind.BEHIND
    assert status.severity == Severity.ATTENTION


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


def test_check_repo_diverged_reports_behind_and_ahead(fixture_repos_diverged, default_branch_name):
    _origin, clone_path = fixture_repos_diverged

    status = git_ops.check_repo(
        RepoConfig(repo_path=clone_path, base_branches=[default_branch_name])
    )

    assert status.failure_reason is None
    branch_status = status.branch_statuses[0]
    assert branch_status.behind > 0
    assert branch_status.ahead_of_base > 0
    assert branch_status.kind == StatusKind.DIVERGED
    assert status.worst_kind == StatusKind.DIVERGED
    assert status.severity == Severity.BLOCKING


def test_check_repo_unpushed_only(fixture_repos_unpushed, default_branch_name):
    _origin, clone_path = fixture_repos_unpushed

    status = git_ops.check_repo(
        RepoConfig(repo_path=clone_path, base_branches=[default_branch_name])
    )

    assert status.failure_reason is None
    assert status.unpushed > 0
    assert status.branch_statuses[0].kind == StatusKind.UP_TO_DATE
    assert status.worst_kind == StatusKind.UNPUSHED
    assert status.severity == Severity.ATTENTION


def test_check_repo_behind_and_unpushed(fixture_repos_behind_and_unpushed, default_branch_name):
    _origin, clone_path = fixture_repos_behind_and_unpushed

    status = git_ops.check_repo(
        RepoConfig(repo_path=clone_path, base_branches=[default_branch_name])
    )

    assert status.failure_reason is None
    assert status.unpushed > 0
    branch_status = status.branch_statuses[0]
    assert branch_status.behind > 0
    assert branch_status.ahead_of_base == 0
    assert branch_status.kind == StatusKind.BEHIND
    assert status.worst_kind == StatusKind.BEHIND_AND_UNPUSHED
    assert status.severity == Severity.ATTENTION


def test_check_repo_multi_base_worst_wins(fixture_repos_multi_base, default_branch_name):
    origin_path, clone_path = fixture_repos_multi_base

    status = git_ops.check_repo(
        RepoConfig(repo_path=clone_path, base_branches=[default_branch_name, "release"])
    )

    assert status.failure_reason is None
    assert len(status.branch_statuses) == 2
    by_base = {bs.base: bs for bs in status.branch_statuses}
    assert by_base[default_branch_name].kind == StatusKind.DIVERGED
    assert by_base["release"].kind == StatusKind.UP_TO_DATE
    assert status.worst_kind == StatusKind.DIVERGED
    assert status.severity == Severity.BLOCKING


def test_unpushed_count_zero_when_no_upstream(fixture_repo_no_upstream):
    count = git_ops.unpushed_count(fixture_repo_no_upstream)

    assert count == 0


def test_check_repo_fetch_failure_is_distinct_not_bogus_behind(
    fixture_repos_fetch_fails, default_branch_name
):
    _origin, clone_path = fixture_repos_fetch_fails

    status = git_ops.check_repo(
        RepoConfig(repo_path=clone_path, base_branches=[default_branch_name])
    )

    branch_status = status.branch_statuses[0]
    assert branch_status.kind == StatusKind.CHECK_FAILED
    assert branch_status.behind == 0
    assert branch_status.reason == "fetch failed — check network/SSH access"
    assert status.worst_kind == StatusKind.CHECK_FAILED
    assert status.severity == Severity.BLOCKING

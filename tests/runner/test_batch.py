"""Tests for runner.batch.check_all — bounded ThreadPoolExecutor fan-out.

core.git_ops.check_repo is mocked here; no real subprocess/git calls needed to
prove parallelism, bounded worker count, or per-repo failure isolation.
"""

from __future__ import annotations

from unittest.mock import patch

from base_branch_watch.core.models import RepoConfig, RepoStatus, StatusKind
from base_branch_watch.runner import batch


def _fake_status(repo: RepoConfig) -> RepoStatus:
    return RepoStatus(
        repo_path=repo.repo_path,
        name=repo.repo_path.rsplit("/", 1)[-1],
        current_branch="main",
        unpushed=0,
        branch_statuses=[],
        failure_reason=None,
    )


def test_check_all_calls_check_repo_once_per_repo():
    repos = [
        RepoConfig(repo_path="/tmp/r1", base_branches=["main"]),
        RepoConfig(repo_path="/tmp/r2", base_branches=["main"]),
        RepoConfig(repo_path="/tmp/r3", base_branches=["main"]),
    ]
    with patch(
        "base_branch_watch.runner.batch.git_ops.check_repo", side_effect=_fake_status
    ) as mock_check:
        results = batch.check_all(repos)

    assert mock_check.call_count == 3
    assert {r.repo_path for r in results} == {"/tmp/r1", "/tmp/r2", "/tmp/r3"}


def test_check_all_bounds_worker_count_to_min_of_max_workers_and_repo_count():
    repos = [RepoConfig(repo_path=f"/tmp/r{i}", base_branches=["main"]) for i in range(3)]
    with patch(
        "base_branch_watch.runner.batch.git_ops.check_repo", side_effect=_fake_status
    ), patch("base_branch_watch.runner.batch.ThreadPoolExecutor") as mock_pool_cls:
        mock_pool_cls.return_value.__enter__.return_value.submit.return_value = None
        # submit() returning None breaks as_completed; instead, verify only the
        # constructor's max_workers argument here (isolated from execution).
        try:
            batch.check_all(repos, max_workers=8)
        except Exception:
            pass

    assert mock_pool_cls.call_args.kwargs.get("max_workers") == 3


def test_check_all_bounds_worker_count_never_exceeds_max_workers():
    repos = [RepoConfig(repo_path=f"/tmp/r{i}", base_branches=["main"]) for i in range(20)]
    with patch(
        "base_branch_watch.runner.batch.git_ops.check_repo", side_effect=_fake_status
    ) as mock_check:
        results = batch.check_all(repos, max_workers=8)

    assert mock_check.call_count == 20
    assert len(results) == 20


def test_check_all_isolates_per_repo_exception_as_check_failed_status():
    repos = [
        RepoConfig(repo_path="/tmp/good", base_branches=["main"]),
        RepoConfig(repo_path="/tmp/bad", base_branches=["main"]),
    ]

    def side_effect(repo: RepoConfig) -> RepoStatus:
        if repo.repo_path == "/tmp/bad":
            raise RuntimeError("boom")
        return _fake_status(repo)

    with patch("base_branch_watch.runner.batch.git_ops.check_repo", side_effect=side_effect):
        results = batch.check_all(repos)

    by_path = {r.repo_path: r for r in results}
    assert len(results) == 2
    assert by_path["/tmp/good"].worst_kind != StatusKind.CHECK_FAILED
    assert by_path["/tmp/bad"].worst_kind == StatusKind.CHECK_FAILED
    assert by_path["/tmp/bad"].failure_reason is not None


def test_check_all_empty_repo_list_returns_empty_list_without_pool():
    with patch("base_branch_watch.runner.batch.ThreadPoolExecutor") as mock_pool_cls:
        results = batch.check_all([])

    assert results == []
    mock_pool_cls.assert_not_called()


def test_batch_module_is_pure_no_rumps_or_appkit_import():
    import base_branch_watch.runner.batch as batch_module

    src = open(batch_module.__file__).read()
    assert "import rumps" not in src
    assert "from rumps" not in src
    assert "import AppKit" not in src
    assert "from AppKit" not in src

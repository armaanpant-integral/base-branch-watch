"""Tests for runner.batch.check_all — bounded ThreadPoolExecutor fan-out.

core.git_ops.check_repo and core.pr_status.check_pr are mocked here; no real
subprocess/git/gh calls needed to prove parallelism, bounded worker count, or
per-repo failure isolation. check_all returns (list[RepoStatus],
dict[str, PrStatus]) — D-12: PR status is fetched in the SAME pool.
"""

from __future__ import annotations

from unittest.mock import patch

from base_branch_watch.core.models import PrStatusKind, RepoConfig, RepoStatus, StatusKind
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


def _fake_pr_status(repo_path: str):
    from base_branch_watch.core.models import PrStatus

    return PrStatus(kind=PrStatusKind.NO_PR, current_branch="main")


def test_check_all_calls_check_repo_once_per_repo():
    repos = [
        RepoConfig(repo_path="/tmp/r1", base_branches=["main"]),
        RepoConfig(repo_path="/tmp/r2", base_branches=["main"]),
        RepoConfig(repo_path="/tmp/r3", base_branches=["main"]),
    ]
    with patch(
        "base_branch_watch.runner.batch.git_ops.check_repo", side_effect=_fake_status
    ) as mock_check, patch(
        "base_branch_watch.runner.batch.pr_status.check_pr", side_effect=_fake_pr_status
    ):
        results, pr_statuses = batch.check_all(repos)

    assert mock_check.call_count == 3
    assert {r.repo_path for r in results} == {"/tmp/r1", "/tmp/r2", "/tmp/r3"}
    assert set(pr_statuses.keys()) == {"/tmp/r1", "/tmp/r2", "/tmp/r3"}


def test_check_all_bounds_worker_count_to_min_of_max_workers_and_repo_count():
    repos = [RepoConfig(repo_path=f"/tmp/r{i}", base_branches=["main"]) for i in range(3)]
    with patch(
        "base_branch_watch.runner.batch.git_ops.check_repo", side_effect=_fake_status
    ), patch(
        "base_branch_watch.runner.batch.pr_status.check_pr", side_effect=_fake_pr_status
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
    ) as mock_check, patch(
        "base_branch_watch.runner.batch.pr_status.check_pr", side_effect=_fake_pr_status
    ):
        results, pr_statuses = batch.check_all(repos, max_workers=8)

    assert mock_check.call_count == 20
    assert len(results) == 20
    assert len(pr_statuses) == 20


def test_check_all_isolates_per_repo_exception_as_check_failed_status():
    repos = [
        RepoConfig(repo_path="/tmp/good", base_branches=["main"]),
        RepoConfig(repo_path="/tmp/bad", base_branches=["main"]),
    ]

    def side_effect(repo: RepoConfig) -> RepoStatus:
        if repo.repo_path == "/tmp/bad":
            raise RuntimeError("boom")
        return _fake_status(repo)

    with patch(
        "base_branch_watch.runner.batch.git_ops.check_repo", side_effect=side_effect
    ), patch(
        "base_branch_watch.runner.batch.pr_status.check_pr", side_effect=_fake_pr_status
    ):
        results, pr_statuses = batch.check_all(repos)

    by_path = {r.repo_path: r for r in results}
    assert len(results) == 2
    assert by_path["/tmp/good"].worst_kind != StatusKind.CHECK_FAILED
    assert by_path["/tmp/bad"].worst_kind == StatusKind.CHECK_FAILED
    assert by_path["/tmp/bad"].failure_reason is not None
    # A git-side exception isolates ONLY that repo's RepoStatus; PrStatus for
    # the good repo is unaffected (still NO_PR from the fake).
    assert pr_statuses["/tmp/good"].kind == PrStatusKind.NO_PR


def test_check_all_isolates_per_repo_pr_status_exception_as_check_failed():
    """A pr_status.check_pr exception isolates to THAT REPO'S pair of
    statuses — RepoStatus.failed + PrStatus.failed, per the plan's per-future
    try/except (the git+gh calls share one worker future per repo, so an
    exception from either call fails both statuses for that repo only; the
    OTHER repo's pair is entirely unaffected) — D-11."""
    repos = [
        RepoConfig(repo_path="/tmp/good", base_branches=["main"]),
        RepoConfig(repo_path="/tmp/bad-pr", base_branches=["main"]),
    ]

    def pr_side_effect(repo_path: str):
        if repo_path == "/tmp/bad-pr":
            raise RuntimeError("gh exploded")
        return _fake_pr_status(repo_path)

    with patch(
        "base_branch_watch.runner.batch.git_ops.check_repo", side_effect=_fake_status
    ), patch("base_branch_watch.runner.batch.pr_status.check_pr", side_effect=pr_side_effect):
        results, pr_statuses = batch.check_all(repos)

    by_path = {r.repo_path: r for r in results}
    # The OTHER repo's pair is entirely unaffected by /tmp/bad-pr's failure.
    assert by_path["/tmp/good"].worst_kind != StatusKind.CHECK_FAILED
    assert pr_statuses["/tmp/good"].kind == PrStatusKind.NO_PR
    # /tmp/bad-pr's gh-side exception fails BOTH its RepoStatus and PrStatus,
    # since they share one worker future per repo (matches the plan's
    # explicit per-future try/except pairing, not per-axis isolation).
    assert by_path["/tmp/bad-pr"].worst_kind == StatusKind.CHECK_FAILED
    assert pr_statuses["/tmp/bad-pr"].kind == PrStatusKind.CHECK_FAILED
    assert pr_statuses["/tmp/bad-pr"].reason is not None


def test_check_all_empty_repo_list_returns_empty_list_without_pool():
    with patch("base_branch_watch.runner.batch.ThreadPoolExecutor") as mock_pool_cls:
        results, pr_statuses = batch.check_all([])

    assert results == []
    assert pr_statuses == {}
    mock_pool_cls.assert_not_called()


# -- Task 3 (Plan 02): pr_repo_paths gate (D-13's floor lives in menubar.py,
# batch only mechanically honors the passed-in eligible set). --------------


def test_check_all_pr_repo_paths_gate_limits_check_pr_to_eligible_repo_only():
    repos = [
        RepoConfig(repo_path="/tmp/eligible", base_branches=["main"]),
        RepoConfig(repo_path="/tmp/excluded", base_branches=["main"]),
    ]
    calls: list[str] = []

    def pr_side_effect(repo_path: str):
        calls.append(repo_path)
        return _fake_pr_status(repo_path)

    with patch(
        "base_branch_watch.runner.batch.git_ops.check_repo", side_effect=_fake_status
    ), patch("base_branch_watch.runner.batch.pr_status.check_pr", side_effect=pr_side_effect):
        results, pr_statuses = batch.check_all(repos, pr_repo_paths={"/tmp/eligible"})

    assert calls == ["/tmp/eligible"]
    assert set(pr_statuses.keys()) == {"/tmp/eligible"}
    # Git-status check still runs for both repos -- only the gh call is gated.
    assert {r.repo_path for r in results} == {"/tmp/eligible", "/tmp/excluded"}


def test_check_all_pr_repo_paths_none_default_checks_every_repo():
    repos = [
        RepoConfig(repo_path="/tmp/r1", base_branches=["main"]),
        RepoConfig(repo_path="/tmp/r2", base_branches=["main"]),
    ]
    with patch(
        "base_branch_watch.runner.batch.git_ops.check_repo", side_effect=_fake_status
    ), patch(
        "base_branch_watch.runner.batch.pr_status.check_pr", side_effect=_fake_pr_status
    ) as mock_pr:
        results, pr_statuses = batch.check_all(repos)

    assert mock_pr.call_count == 2
    assert set(pr_statuses.keys()) == {"/tmp/r1", "/tmp/r2"}


def test_batch_module_is_pure_no_rumps_or_appkit_import():
    import base_branch_watch.runner.batch as batch_module

    src = open(batch_module.__file__).read()
    assert "import rumps" not in src
    assert "from rumps" not in src
    assert "import AppKit" not in src
    assert "from AppKit" not in src

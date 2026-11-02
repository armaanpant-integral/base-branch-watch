"""assess_base: one per-(repo, base) verdict shared by the poller and the push gate.

Real tmp git repos throughout (see tests/conftest.py fixtures); the verdict
reports facts only - gating and menu status are decided by the callers.
"""

from __future__ import annotations

from pathlib import Path

from base_branch_watch.core import base_verdict, git_ops


def _assess(clone_path, base, local_ref="HEAD", **kwargs):
    return base_verdict.assess_base(clone_path, base, local_ref, **kwargs)


def test_behind_clone_reports_counts_and_post_fetch_head_sha(fixture_repos, default_branch_name):
    _origin, clone_path = fixture_repos

    verdict = _assess(clone_path, default_branch_name)

    assert verdict.fetch_ok is True
    assert verdict.fetch_error is None
    assert verdict.behind > 0
    assert verdict.ahead == 0
    assert verdict.head_sha == git_ops.resolve_ref(clone_path, f"origin/{default_branch_name}")
    assert verdict.merge_conflict is False  # nothing of ours to conflict with
    assert verdict.incoming_commits == []  # opt-in, not paid on the poll path


def test_up_to_date_clone_has_no_incoming_and_no_conflict(
    fixture_repos_up_to_date, default_branch_name
):
    _origin, clone_path = fixture_repos_up_to_date

    verdict = _assess(clone_path, default_branch_name)

    assert (verdict.behind, verdict.ahead) == (0, 0)
    assert verdict.merge_conflict is False
    assert verdict.working_tree_overlap == []


def test_real_merge_conflict_is_reported_with_paths(
    fixture_repos_conflict_overlap, default_branch_name
):
    _origin, clone_path = fixture_repos_conflict_overlap

    verdict = _assess(clone_path, default_branch_name)

    assert verdict.merge_conflict is True
    assert verdict.conflict_paths == ["shared.txt"]


def test_branch_only_path_overlap_without_hunk_conflict_is_a_clean_merge(
    fixture_repos_incoming_rename, default_branch_name
):
    """Origin renames file.txt while we edit it: git merges that cleanly, so
    the verdict must not call it a conflict (the old path-overlap heuristic did)."""
    _origin, clone_path = fixture_repos_incoming_rename

    verdict = _assess(clone_path, default_branch_name)

    assert verdict.behind > 0 and verdict.ahead > 0
    assert verdict.merge_conflict is False


def test_uncommitted_edit_to_an_incoming_path_is_a_working_tree_overlap(
    fixture_repos_behind_no_overlap, default_branch_name
):
    _origin, clone_path = fixture_repos_behind_no_overlap
    (Path(clone_path) / "shared.txt").write_text("uncommitted local edit\n")

    verdict = _assess(clone_path, default_branch_name)

    assert verdict.working_tree_overlap == ["shared.txt"]
    assert verdict.merge_conflict is False  # merge-tree can't see uncommitted edits


def test_fetch_failure_is_data_not_a_guess(fixture_repos_fetch_fails, default_branch_name):
    _origin, clone_path = fixture_repos_fetch_fails

    verdict = _assess(clone_path, default_branch_name)

    assert verdict.fetch_ok is False
    assert verdict.fetch_error
    assert verdict.head_sha is None
    assert verdict.merge_conflict is None  # stale origin ref proves nothing


def test_unrelated_histories_are_undeterminable_not_clean(
    fixture_repos_no_common_ancestor, default_branch_name
):
    _origin, clone_path = fixture_repos_no_common_ancestor

    verdict = _assess(clone_path, default_branch_name)

    assert verdict.merge_conflict is None
    assert verdict.paths_determinable is False


def test_local_ref_is_honoured_instead_of_head(fixture_repos_conflict_overlap, default_branch_name):
    """The hook assesses the pushed SHA, not the checked-out HEAD."""
    _origin, clone_path = fixture_repos_conflict_overlap

    verdict = _assess(clone_path, default_branch_name, local_ref=f"origin/{default_branch_name}")

    assert (verdict.behind, verdict.ahead) == (0, 0)
    assert verdict.merge_conflict is False


def test_detailed_assessment_adds_incoming_commits_and_local_changes(
    fixture_repos_conflict_overlap, default_branch_name
):
    _origin, clone_path = fixture_repos_conflict_overlap

    verdict = _assess(clone_path, default_branch_name, detailed=True)

    assert [c.subject for c in verdict.incoming_commits] == ["origin advances shared.txt"]
    assert verdict.local_changes == {"shared.txt", "file.txt"}


def test_detailed_assessment_of_a_failed_fetch_stays_unfetched_and_undecided(
    fixture_repos_fetch_fails, default_branch_name
):
    """The push summary may still read the stale origin ref, but nothing may
    mistake that for a fresh answer: fetch_ok stays False, no head SHA, and
    merge_conflict is undeterminable rather than a (stale) clean/conflict."""
    _origin, clone_path = fixture_repos_fetch_fails

    verdict = _assess(clone_path, default_branch_name, detailed=True)

    assert verdict.fetch_ok is False
    assert verdict.head_sha is None
    assert verdict.merge_conflict is None


def _merge_tree_times_out(recorded: list[int]):
    """A _run_git stand-in: merge-tree times out (recording the timeout it was
    given); every other git call runs for real."""
    import subprocess

    real_run_git = git_ops._run_git

    def fake(repo_path, args, timeout, **kwargs):
        if args[0] == "merge-tree":
            recorded.append(timeout)
            raise subprocess.TimeoutExpired(cmd="git", timeout=timeout)
        return real_run_git(repo_path, args, timeout, **kwargs)

    return fake


def test_config_json_timeout_reaches_the_merge_tree_subprocess_via_check_repo(
    fixture_repos_conflict_overlap, default_branch_name, bbw_config_dir
):
    from unittest.mock import patch

    from base_branch_watch.core import config
    from base_branch_watch.core.models import RepoConfig

    _origin, clone_path = fixture_repos_conflict_overlap
    config.save_config(
        config.AppConfig(
            repos=[
                RepoConfig(
                    repo_path=clone_path,
                    base_branches=[default_branch_name],
                    merge_tree_timeout_seconds=7,
                )
            ]
        )
    )
    repo = config.load_config().repos[0]
    recorded: list[int] = []

    with patch("base_branch_watch.core.git_ops._run_git", _merge_tree_times_out(recorded)):
        status = base_verdict.check_repo(repo)
        verdict = base_verdict.assess_base(
            clone_path,
            default_branch_name,
            merge_tree_timeout=repo.merge_tree_timeout_seconds,
        )

    assert recorded == [7, 7]
    assert verdict.merge_conflict is None
    assert verdict.merge_tree_timed_out is True
    assert verdict.merge_tree_timeout_seconds == 7
    assert status.branch_statuses[0].base == default_branch_name


def test_timeout_with_undeterminable_paths_reports_timed_out_not_local_git_error(
    fixture_repos_conflict_overlap, default_branch_name
):
    from unittest.mock import patch

    from base_branch_watch.core.models import RepoConfig

    _origin, clone_path = fixture_repos_conflict_overlap
    repo = RepoConfig(
        repo_path=clone_path, base_branches=[default_branch_name], merge_tree_timeout_seconds=7
    )

    with (
        patch("base_branch_watch.core.git_ops._run_git", _merge_tree_times_out([])),
        patch("base_branch_watch.core.git_ops.merge_base", return_value=None),
    ):
        status = base_verdict.check_repo(repo)

    assert status.branch_statuses[0].reason == "conflict check timed out after 7s"


def test_non_timeout_merge_tree_failure_still_reports_local_git_error(
    fixture_repos_conflict_overlap, default_branch_name
):
    import subprocess
    from unittest.mock import patch

    from base_branch_watch.core.models import RepoConfig

    _origin, clone_path = fixture_repos_conflict_overlap
    repo = RepoConfig(repo_path=clone_path, base_branches=[default_branch_name])
    real_run_git = git_ops._run_git

    def merge_tree_exits_128(repo_path, args, timeout, **kwargs):
        if args[0] == "merge-tree":
            return subprocess.CompletedProcess(args, 128, b"", b"fatal")
        return real_run_git(repo_path, args, timeout, **kwargs)

    with (
        patch("base_branch_watch.core.git_ops._run_git", merge_tree_exits_128),
        patch("base_branch_watch.core.git_ops.merge_base", return_value=None),
    ):
        status = base_verdict.check_repo(repo)

    assert status.branch_statuses[0].reason == "conflict check failed - local git error"

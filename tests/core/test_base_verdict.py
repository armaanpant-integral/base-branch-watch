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


def test_local_ref_is_honoured_instead_of_head(
    fixture_repos_conflict_overlap, default_branch_name
):
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

"""git_ops branch queries used by stale-branch detection, against real tmp git repos."""

from __future__ import annotations

import os

from base_branch_watch.core import git_ops
from tests.core.git_helpers import commit_on_new_branch as _commit_on_new_branch
from tests.core.git_helpers import git as _git


def test_remote_heads_maps_every_origin_branch_to_its_sha(
    fixture_repos_up_to_date, default_branch_name
):
    _origin, clone = fixture_repos_up_to_date
    main_sha = _git(clone, "rev-parse", default_branch_name)
    feature_sha = _commit_on_new_branch(clone, "feat/x")

    heads = git_ops.remote_heads(clone)

    assert heads == {default_branch_name: main_sha, "feat/x": feature_sha}


def test_local_branches_reports_sha_upstream_and_checked_out_worktree(
    fixture_repos_up_to_date, default_branch_name
):
    _origin, clone = fixture_repos_up_to_date
    feature_sha = _commit_on_new_branch(clone, "feat/x")
    _git(clone, "switch", default_branch_name)
    _commit_on_new_branch(clone, "local-only", push=False)
    _git(clone, "switch", default_branch_name)

    branches = {branch.name: branch for branch in git_ops.local_branches(clone)}

    assert set(branches) == {default_branch_name, "feat/x", "local-only"}
    pushed = branches["feat/x"]
    assert (pushed.sha, pushed.upstream_remote, pushed.upstream_ref) == (
        feature_sha,
        "origin",
        "refs/heads/feat/x",
    )
    assert pushed.worktree_path is None
    unpushed = branches["local-only"]
    assert (unpushed.upstream_remote, unpushed.upstream_ref) == (None, None)
    assert os.path.realpath(branches[default_branch_name].worktree_path) == os.path.realpath(clone)


def test_is_ancestor_true_for_merged_history_false_for_unmerged_work(
    fixture_repos_up_to_date, default_branch_name
):
    _origin, clone = fixture_repos_up_to_date
    main_sha = _git(clone, "rev-parse", default_branch_name)
    feature_sha = _commit_on_new_branch(clone, "feat/x")

    assert git_ops.is_ancestor(clone, main_sha, feature_sha) is True
    assert git_ops.is_ancestor(clone, feature_sha, main_sha) is False

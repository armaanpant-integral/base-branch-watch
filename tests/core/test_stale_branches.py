"""Stale-branch classification: pure data in, kinds out. No git, no I/O."""

from __future__ import annotations

from base_branch_watch.core import stale_branches
from base_branch_watch.core.git_ops import LocalBranch
from base_branch_watch.core.models import RepoConfig, StaleBranch, StaleKind
from tests.core.git_helpers import commit_on_new_branch, git


def _branch(name: str, upstream: str | None = "origin") -> LocalBranch:
    return LocalBranch(
        name=name,
        sha=f"sha-{name}",
        upstream_remote=upstream,
        upstream_ref=f"refs/heads/{name}" if upstream else None,
        worktree_path=None,
    )


def test_branch_whose_origin_branch_is_gone_and_is_merged_is_gone_merged():
    result = stale_branches.classify(
        [_branch("feat/x")], remote_heads={"main": "sha-main"}, is_merged=lambda sha: True
    )

    assert result == [StaleBranch(name="feat/x", kind=StaleKind.GONE_MERGED)]


def test_branch_with_no_upstream_is_never_stale_even_when_merged():
    result = stale_branches.classify(
        [_branch("fresh-local", upstream=None)],
        remote_heads={"main": "sha-main"},
        is_merged=lambda sha: True,
    )

    assert result == []


def test_reader_measures_merged_against_the_watched_base_on_origin():
    merged_into: dict[str, str] = {}

    def is_ancestor(_repo, branch_sha, base_ref):
        merged_into[branch_sha] = base_ref
        return base_ref == "origin/main"

    reader = stale_branches.StaleBranchReader(
        list_branches=lambda _repo: [_branch("feat/x")],
        remote_heads=lambda _repo: {"main": "sha-main"},
        is_ancestor=is_ancestor,
    )

    result = reader.check(RepoConfig(repo_path="/r", base_branches=["main"]))

    assert result == [StaleBranch(name="feat/x", kind=StaleKind.GONE_MERGED)]
    assert merged_into == {"sha-feat/x": "origin/main"}


def test_real_repo_flags_only_the_branch_merged_and_deleted_on_origin(
    fixture_repos_up_to_date, default_branch_name
):
    origin, clone = fixture_repos_up_to_date
    commit_on_new_branch(clone, "feat/x")
    git(clone, "switch", default_branch_name)
    commit_on_new_branch(clone, "wip", push=False)
    git(clone, "switch", default_branch_name)
    # What a merged PR does on the server: merge the branch into main, delete the branch.
    git(origin, "merge", "--no-ff", "--no-edit", "feat/x")
    git(origin, "branch", "-D", "feat/x")
    git(clone, "fetch", "--quiet", "origin")  # the app's own per-cycle fetch of the base

    result = stale_branches.StaleBranchReader().check(
        RepoConfig(repo_path=clone, base_branches=[default_branch_name])
    )

    assert result == [StaleBranch(name="feat/x", kind=StaleKind.GONE_MERGED)]

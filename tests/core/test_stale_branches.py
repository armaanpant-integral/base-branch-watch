"""Stale-branch classification: pure data in, kinds out. No git, no I/O."""

from __future__ import annotations

from base_branch_watch.core import stale_branches
from base_branch_watch.core.git_ops import LocalBranch
from base_branch_watch.core.models import RepoConfig, StaleBranch, StaleKind
from tests.core.git_helpers import commit_on_new_branch, git


def _branch(
    name: str, upstream: str | None = "origin", worktree_path: str | None = None
) -> LocalBranch:
    return LocalBranch(
        name=name,
        sha=f"sha-{name}",
        upstream_remote=upstream,
        upstream_ref=f"refs/heads/{name}" if upstream else None,
        worktree_path=worktree_path,
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


def test_gone_upstream_with_commits_missing_from_base_is_gone_unmerged():
    result = stale_branches.classify(
        [_branch("feat/x")], remote_heads={"main": "sha-main"}, is_merged=lambda sha: False
    )

    assert result == [StaleBranch(name="feat/x", kind=StaleKind.GONE_UNMERGED)]


def test_branch_merged_into_base_whose_origin_branch_still_exists_is_merged():
    result = stale_branches.classify(
        [_branch("feat/x")],
        remote_heads={"main": "sha-main", "feat/x": "sha-feat/x"},
        is_merged=lambda sha: True,
    )

    assert result == [StaleBranch(name="feat/x", kind=StaleKind.MERGED)]


def test_a_branch_checked_out_in_any_worktree_is_never_stale():
    result = stale_branches.classify(
        [_branch("current", worktree_path="/r"), _branch("other-wt", worktree_path="/r-wt")],
        remote_heads={"main": "sha-main"},
        is_merged=lambda sha: True,
    )

    assert result == []


def test_a_watched_base_branch_is_never_stale_even_when_not_checked_out():
    result = stale_branches.classify(
        [_branch("release")],
        remote_heads={"main": "sha-main"},
        is_merged=lambda sha: True,
        watched_bases={"release"},
    )

    assert result == []


def test_reader_never_flags_the_repos_own_watched_base_branches():
    reader = stale_branches.StaleBranchReader(
        list_branches=lambda _repo: [_branch("release"), _branch("feat/x")],
        remote_heads=lambda _repo: {"main": "sha-main"},
        is_ancestor=lambda _repo, _sha, _base: True,
    )

    result = reader.check(RepoConfig(repo_path="/r", base_branches=["main", "release"]))

    assert [branch.name for branch in result] == ["feat/x"]


def test_a_pushed_branch_still_sitting_on_the_base_tip_is_not_called_merged():
    result = stale_branches.classify(
        [_branch("feat/new")],
        remote_heads={"main": "sha-main", "feat/new": "sha-feat/new"},
        is_merged=lambda sha: True,
        at_base_tip=lambda sha: True,
    )

    assert result == []


def test_reader_treats_a_branch_on_a_watched_base_tip_as_not_merged():
    reader = stale_branches.StaleBranchReader(
        list_branches=lambda _repo: [_branch("feat/new")],
        remote_heads=lambda _repo: {"main": "sha-main", "feat/new": "sha-feat/new"},
        is_ancestor=lambda _repo, _sha, _base: True,
        resolve_ref=lambda _repo, ref: "sha-feat/new" if ref == "origin/main" else None,
    )

    assert reader.check(RepoConfig(repo_path="/r", base_branches=["main"])) == []


def test_real_repo_labels_each_kind_and_skips_everything_that_is_not_stale(
    fixture_repos_up_to_date, default_branch_name
):
    origin, clone = fixture_repos_up_to_date
    for name in ("feat/merged-gone", "feat/squashed-gone", "feat/merged-kept"):
        commit_on_new_branch(clone, name)
        git(clone, "switch", default_branch_name)
    commit_on_new_branch(clone, "wip", push=False)  # never pushed: no upstream
    git(clone, "switch", default_branch_name)
    # Server side: two PRs merged; one branch deleted, one left; the third PR squash-merged
    # (its commits never reach main) and its branch deleted.
    git(origin, "merge", "--no-ff", "--no-edit", "feat/merged-gone")
    git(origin, "merge", "--no-ff", "--no-edit", "feat/merged-kept")
    git(origin, "branch", "-D", "feat/merged-gone")
    git(origin, "branch", "-D", "feat/squashed-gone")
    git(clone, "fetch", "--quiet", "origin")
    # Pushed straight from the base tip with no commits yet.
    git(clone, "switch", "-c", "fresh-pushed", f"origin/{default_branch_name}")
    git(clone, "push", "--quiet", "-u", "origin", "fresh-pushed")
    git(clone, "switch", default_branch_name)

    result = stale_branches.StaleBranchReader().check(
        RepoConfig(repo_path=clone, base_branches=[default_branch_name])
    )

    assert {branch.name: branch.kind for branch in result} == {
        "feat/merged-gone": StaleKind.GONE_MERGED,
        "feat/squashed-gone": StaleKind.GONE_UNMERGED,
        "feat/merged-kept": StaleKind.MERGED,
    }


def test_reader_reports_unknown_not_stale_when_origin_heads_cannot_be_read():
    reader = stale_branches.StaleBranchReader(
        list_branches=lambda _repo: [_branch("feat/x")],
        remote_heads=lambda _repo: None,
        is_ancestor=lambda _repo, _sha, _base: True,
    )

    assert reader.check(RepoConfig(repo_path="/r", base_branches=["main"])) is None


def test_a_branch_tracking_a_remote_other_than_origin_is_never_judged_against_origin():
    result = stale_branches.classify(
        [_branch("feat/x", upstream="fork")],
        remote_heads={"main": "sha-main"},
        is_merged=lambda sha: True,
    )

    assert result == []


def test_reader_reports_unknown_when_local_branches_cannot_be_listed():
    reader = stale_branches.StaleBranchReader(
        list_branches=lambda _repo: None,
        remote_heads=lambda _repo: {"main": "sha-main"},
        is_ancestor=lambda _repo, _sha, _base: True,
    )

    assert reader.check(RepoConfig(repo_path="/r", base_branches=["main"])) is None


def test_reader_reports_unknown_when_merged_state_cannot_be_determined():
    reader = stale_branches.StaleBranchReader(
        list_branches=lambda _repo: [_branch("feat/x")],
        remote_heads=lambda _repo: {"main": "sha-main"},
        is_ancestor=lambda _repo, _sha, _base: None,  # e.g. origin/main was never fetched
    )

    assert reader.check(RepoConfig(repo_path="/r", base_branches=["main"])) is None


def test_an_upstream_that_is_not_a_branch_ref_is_never_read_as_gone():
    tracking_a_tag = LocalBranch(
        name="feat/x",
        sha="sha-x",
        upstream_remote="origin",
        upstream_ref="refs/tags/v1",
        worktree_path=None,
    )

    result = stale_branches.classify(
        [tracking_a_tag], remote_heads={"main": "sha-main"}, is_merged=lambda sha: True
    )

    assert result == []


def test_a_repo_with_no_watched_base_branches_is_unknown_not_everything_unmerged():
    reader = stale_branches.StaleBranchReader(
        list_branches=lambda _repo: [_branch("feat/x")],
        remote_heads=lambda _repo: {"main": "sha-main"},
        is_ancestor=lambda _repo, _sha, _base: True,
    )

    assert reader.check(RepoConfig(repo_path="/r", base_branches=[])) is None


def test_merged_into_any_one_watched_base_counts_as_merged():
    reader = stale_branches.StaleBranchReader(
        list_branches=lambda _repo: [_branch("feat/x")],
        remote_heads=lambda _repo: {"main": "m", "release": "r", "feat/x": "sha-feat/x"},
        is_ancestor=lambda _repo, _sha, base_ref: base_ref == "origin/release",
        resolve_ref=lambda _repo, _ref: None,
    )

    result = reader.check(RepoConfig(repo_path="/r", base_branches=["main", "release"]))

    assert result == [StaleBranch(name="feat/x", kind=StaleKind.MERGED)]


def test_real_repo_handles_unicode_and_hash_branch_names_and_a_detached_head(
    fixture_repos_up_to_date, default_branch_name
):
    origin, clone = fixture_repos_up_to_date
    for name in ("feat/café-#12", "feat/plain"):
        commit_on_new_branch(clone, name)
        git(clone, "switch", default_branch_name)
    git(origin, "merge", "--no-ff", "--no-edit", "feat/café-#12")
    git(origin, "merge", "--no-ff", "--no-edit", "feat/plain")
    git(origin, "branch", "-D", "feat/café-#12")
    git(origin, "branch", "-D", "feat/plain")
    git(clone, "fetch", "--quiet", "origin")
    git(clone, "switch", "--detach", f"origin/{default_branch_name}")  # detached HEAD

    result = stale_branches.StaleBranchReader().check(
        RepoConfig(repo_path=clone, base_branches=[default_branch_name])
    )

    assert {branch.name: branch.kind for branch in result} == {
        "feat/café-#12": StaleKind.GONE_MERGED,
        "feat/plain": StaleKind.GONE_MERGED,
    }


def _two_base_reader(answers: dict[str, bool | None]) -> stale_branches.StaleBranchReader:
    return stale_branches.StaleBranchReader(
        list_branches=lambda _repo: [_branch("feat/x")],
        remote_heads=lambda _repo: {"main": "m", "release": "r", "feat/x": "sha-feat/x"},
        is_ancestor=lambda _repo, _sha, base_ref: answers[base_ref],
        resolve_ref=lambda _repo, _ref: None,
    )


def test_merged_into_the_first_base_but_not_the_second_still_counts_as_merged():
    reader = _two_base_reader({"origin/main": True, "origin/release": False})

    result = reader.check(RepoConfig(repo_path="/r", base_branches=["main", "release"]))

    assert result == [StaleBranch(name="feat/x", kind=StaleKind.MERGED)]


def test_one_base_that_cannot_be_measured_makes_the_whole_repo_unknown():
    reader = _two_base_reader({"origin/main": False, "origin/release": None})

    assert reader.check(RepoConfig(repo_path="/r", base_branches=["main", "release"])) is None

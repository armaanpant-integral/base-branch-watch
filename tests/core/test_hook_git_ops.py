from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from base_branch_watch.core import git_ops

GIT = "/usr/bin/git"


def test_merge_tree_dry_run_clean_merge_returns_false_empty(fixture_repos, default_branch_name):
    _origin, clone_path = fixture_repos
    git_ops.fetch(clone_path, default_branch_name)

    result = git_ops.merge_tree_dry_run(clone_path, "HEAD", f"origin/{default_branch_name}")

    assert result == (False, [])


def test_merge_tree_dry_run_detects_real_conflict(
    fixture_repos_conflict_overlap, default_branch_name
):
    _origin, clone_path = fixture_repos_conflict_overlap
    git_ops.fetch(clone_path, default_branch_name)

    result = git_ops.merge_tree_dry_run(clone_path, "HEAD", f"origin/{default_branch_name}")

    assert result is not None
    has_conflicts, paths = result
    assert has_conflicts is True
    assert "shared.txt" in paths


def test_merge_tree_dry_run_no_common_ancestor_returns_none(
    fixture_repos_no_common_ancestor, default_branch_name
):
    _origin, clone_path = fixture_repos_no_common_ancestor
    git_ops.fetch(clone_path, default_branch_name)

    result = git_ops.merge_tree_dry_run(clone_path, "HEAD", f"origin/{default_branch_name}")

    assert result is None


def test_merge_tree_dry_run_unresolvable_ref_returns_none_not_conflict(fixture_repos):
    """RESEARCH.md Pitfall 3b: an unfetched/nonexistent base ref also exits 1,
    but with completely empty stdout -- this is NOT a conflict and must not
    be parsed as (True, [])."""
    _origin, clone_path = fixture_repos

    result = git_ops.merge_tree_dry_run(clone_path, "HEAD", "origin/does-not-exist")

    assert result is None


def test_merge_tree_dry_run_rejects_dash_prefixed_branch_as_flag(fixture_repos):
    """Argument-injection guard mirroring test_fetch_rejects_dash_prefixed_base_as_flag:
    with the '--' separator in place, a flag-shaped branch string is treated
    as a literal (nonexistent) ref, never as a real merge-tree flag."""
    _origin, clone_path = fixture_repos

    result = git_ops.merge_tree_dry_run(clone_path, "HEAD", "--stdin")

    assert result is None


def test_hooks_path_resolves_default_dot_git_hooks_dir(fixture_repos):
    _origin, clone_path = fixture_repos

    result = git_ops.hooks_path(clone_path)

    assert result is not None
    assert Path(result) == Path(clone_path) / ".git" / "hooks"


def test_hooks_path_reflects_core_hooks_path_override(fixture_repos):
    _origin, clone_path = fixture_repos
    subprocess.run(
        [GIT, "-C", clone_path, "config", "core.hooksPath", ".myhooks"],
        check=True,
        capture_output=True,
        text=True,
        timeout=10,
    )

    result = git_ops.hooks_path(clone_path)

    assert result is not None
    assert Path(result) == Path(clone_path) / ".myhooks"


def test_repo_toplevel_returns_clone_root(fixture_repos):
    import os

    _origin, clone_path = fixture_repos

    result = git_ops.repo_toplevel(clone_path)

    assert result is not None
    assert os.path.realpath(result) == os.path.realpath(clone_path)


@pytest.mark.parametrize(
    "stdin_text,expected",
    [
        (
            "refs/heads/feature ef1f2bc34567ade2af4fc063e4f849c4ac97b31c "
            "refs/heads/feature 0000000000000000000000000000000000000000\n",
            [
                (
                    "refs/heads/feature",
                    "ef1f2bc34567ade2af4fc063e4f849c4ac97b31c",
                    "refs/heads/feature",
                    "0" * 40,
                )
            ],
        ),
        (
            "(delete) 0000000000000000000000000000000000000000 "
            "refs/heads/feature ef1f2bc34567ade2af4fc063e4f849c4ac97b31c\n",
            [
                (
                    "(delete)",
                    "0" * 40,
                    "refs/heads/feature",
                    "ef1f2bc34567ade2af4fc063e4f849c4ac97b31c",
                )
            ],
        ),
        (
            "refs/heads/b1 aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa "
            "refs/heads/b1 0000000000000000000000000000000000000000\n"
            "refs/heads/b2 bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb "
            "refs/heads/b2 0000000000000000000000000000000000000000\n",
            [
                (
                    "refs/heads/b1",
                    "a" * 40,
                    "refs/heads/b1",
                    "0" * 40,
                ),
                (
                    "refs/heads/b2",
                    "b" * 40,
                    "refs/heads/b2",
                    "0" * 40,
                ),
            ],
        ),
        ("\n", []),
        ("garbage line with too few fields\n", []),
    ],
)
def test_parse_pre_push_stdin_two_ref_and_delete(stdin_text, expected):
    assert git_ops.parse_pre_push_stdin(stdin_text) == expected


def test_is_delete_detects_zero_sha():
    assert git_ops.is_delete("refs/heads/x", git_ops.ZERO_SHA) is True
    assert git_ops.is_delete("refs/heads/x", "a" * 40) is False


def test_incoming_commits_returns_commit_with_its_own_changed_paths(
    fixture_repos_conflict_overlap, default_branch_name
):
    _origin, clone_path = fixture_repos_conflict_overlap
    git_ops.fetch(clone_path, default_branch_name)
    origin_ref = f"origin/{default_branch_name}"
    mb = git_ops.merge_base(clone_path, "HEAD", origin_ref)
    assert mb is not None

    commits = git_ops.incoming_commits(clone_path, mb, origin_ref)

    assert len(commits) == 1
    commit = commits[0]
    assert commit.short_hash
    assert commit.author == "Test"
    assert commit.subject == "origin advances shared.txt"
    assert commit.changed_paths == {"shared.txt"}


def test_incoming_commits_returns_empty_on_bad_ref(fixture_repos, default_branch_name):
    _origin, clone_path = fixture_repos

    commits = git_ops.incoming_commits(clone_path, "does-not-exist", "origin/does-not-exist")

    assert commits == []

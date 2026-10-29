"""Decide which local branches are stale. No rumps/AppKit import, ever.

`classify` is pure: it takes already-gathered facts (local branches, origin's live
heads) and a merged-into-base predicate, and returns the stale ones.
"""

from __future__ import annotations

from collections.abc import Callable

from base_branch_watch.core import git_ops
from base_branch_watch.core.git_ops import LocalBranch
from base_branch_watch.core.models import RepoConfig, StaleBranch, StaleKind


def classify(
    branches: list[LocalBranch],
    remote_heads: dict[str, str],
    is_merged: Callable[[str], bool],
) -> list[StaleBranch]:
    stale = []
    for branch in branches:
        if branch.upstream_ref is None:
            continue
        upstream_gone = branch.upstream_ref.removeprefix("refs/heads/") not in remote_heads
        if upstream_gone and is_merged(branch.sha):
            stale.append(StaleBranch(name=branch.name, kind=StaleKind.GONE_MERGED))
    return stale


class StaleBranchReader:
    """Gathers the git facts for one repo and hands them to `classify`.

    The three git functions are constructor arguments, so tests need no patching.
    """

    def __init__(
        self,
        list_branches: Callable[[str], list[LocalBranch]] = git_ops.local_branches,
        remote_heads: Callable[[str], dict[str, str]] = git_ops.remote_heads,
        is_ancestor: Callable[[str, str, str], bool] = git_ops.is_ancestor,
    ) -> None:
        self._list_branches = list_branches
        self._remote_heads = remote_heads
        self._is_ancestor = is_ancestor

    def check(self, repo: RepoConfig) -> list[StaleBranch]:
        def is_merged(branch_sha: str) -> bool:
            return any(
                self._is_ancestor(repo.repo_path, branch_sha, f"origin/{base}")
                for base in repo.base_branches
            )

        return classify(
            self._list_branches(repo.repo_path), self._remote_heads(repo.repo_path), is_merged
        )

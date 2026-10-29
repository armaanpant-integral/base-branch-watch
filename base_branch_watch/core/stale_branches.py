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
    watched_bases: frozenset[str] | set[str] = frozenset(),
    at_base_tip: Callable[[str], bool] = lambda sha: False,
) -> list[StaleBranch]:
    stale = []
    for branch in branches:
        if (
            branch.upstream_ref is None
            or not branch.upstream_ref.startswith("refs/heads/")
            or branch.upstream_remote != "origin"
            or branch.worktree_path is not None
            or branch.name in watched_bases
        ):
            continue
        upstream_gone = branch.upstream_ref.removeprefix("refs/heads/") not in remote_heads
        if upstream_gone:
            kind = StaleKind.GONE_MERGED if is_merged(branch.sha) else StaleKind.GONE_UNMERGED
            stale.append(StaleBranch(name=branch.name, kind=kind))
        elif is_merged(branch.sha) and not at_base_tip(branch.sha):
            stale.append(StaleBranch(name=branch.name, kind=StaleKind.MERGED))
    return stale


class _MergedStateUnknown(Exception):
    """git could not say whether a branch is merged into a watched base (e.g. that base
    was never fetched), so the whole repo's stale state is unknown."""


class StaleBranchReader:
    """Gathers the git facts for one repo and hands them to `classify`.

    The three git functions are constructor arguments, so tests need no patching.
    """

    def __init__(
        self,
        list_branches: Callable[[str], list[LocalBranch] | None] = git_ops.local_branches,
        remote_heads: Callable[[str], dict[str, str] | None] = git_ops.remote_heads,
        is_ancestor: Callable[[str, str, str], bool | None] = git_ops.is_ancestor,
        resolve_ref: Callable[[str, str], str | None] = git_ops.resolve_ref,
    ) -> None:
        self._list_branches = list_branches
        self._remote_heads = remote_heads
        self._is_ancestor = is_ancestor
        self._resolve_ref = resolve_ref

    def check(self, repo: RepoConfig) -> list[StaleBranch] | None:
        """Stale branches of one repo, or None when origin's branches could not be read.

        None is "unknown", deliberately distinct from [] ("nothing stale"): without
        origin's live branch list there is no way to tell which upstreams are gone.
        """
        if not repo.base_branches:
            return None  # nothing to measure "merged" against
        remote_heads = self._remote_heads(repo.repo_path)
        branches = self._list_branches(repo.repo_path)
        if remote_heads is None or branches is None:
            return None

        base_refs = [f"origin/{base}" for base in repo.base_branches]
        base_tips = {self._resolve_ref(repo.repo_path, ref) for ref in base_refs}

        def is_merged(branch_sha: str) -> bool:
            merged = False
            for base_ref in base_refs:
                answer = self._is_ancestor(repo.repo_path, branch_sha, base_ref)
                if answer is None:
                    raise _MergedStateUnknown(base_ref)
                merged = merged or answer
            return merged

        def at_base_tip(branch_sha: str) -> bool:
            return branch_sha in base_tips

        try:
            return classify(
                branches,
                remote_heads,
                is_merged,
                watched_bases=set(repo.base_branches),
                at_base_tip=at_base_tip,
            )
        except _MergedStateUnknown:
            return None

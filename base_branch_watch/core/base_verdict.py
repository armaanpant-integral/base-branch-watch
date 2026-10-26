"""One verdict per (repo, base): the facts both the menu poller and the
pre-push gate decide from.

`assess_base` owns fetch-with-retry, the behind/ahead comparison, the
authoritative `git merge-tree` dry run for committed work, and the
working-tree overlap that merge-tree cannot see. It reports facts only -
what a fetch failure or a conflict means (menu status, push gate, prompt
text) stays with the caller. `check_repo` is the poller's caller: it maps
verdicts onto the menu's BranchStatus/RepoStatus.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field

from base_branch_watch.core import git_ops
from base_branch_watch.core.models import (
    BranchStatus,
    IncomingCommit,
    RepoConfig,
    RepoStatus,
    StatusKind,
)

_REASON_REPO_MISSING = "repo folder not found - was it moved or deleted?"
_REASON_NO_BASES = "no base branches configured"
_REASON_STATUS_FAILED = "status check failed - local git error"
_REASON_CONFLICT_CHECK_FAILED = "conflict check failed - local git error"


@dataclass(frozen=True)
class BaseVerdict:
    base: str
    fetch_ok: bool
    fetch_error: str | None = None
    head_sha: str | None = None
    """`origin/<base>` after the fetch; None when the fetch failed."""
    comparison_ok: bool = True
    """False when the local behind/ahead comparison itself errored."""
    behind: int = 0
    ahead: int = 0
    merge_conflict: bool | None = None
    """Committed work only: True/False from merge-tree, None = undeterminable
    (fetch failed, comparison failed, unrelated histories, timeout)."""
    conflict_paths: list[str] = field(default_factory=list)
    """Paths merge-tree reports as conflicted (git's own order)."""
    working_tree_overlap: list[str] = field(default_factory=list)
    """Sorted incoming paths that also have uncommitted local edits."""
    path_overlap: list[str] = field(default_factory=list)
    """Sorted incoming-vs-(uncommitted + branch-unique) path overlap - the
    fallback signal when merge-tree is undeterminable."""
    paths_determinable: bool = True
    """False when any path set behind the overlap fields could not be read."""
    local_changes: set[str] = field(default_factory=set)
    """Uncommitted + branch-unique paths (detailed only), for the push
    summary's per-commit overlap flag."""
    incoming_commits: list[IncomingCommit] = field(default_factory=list)
    """Commits on origin/<base> not in local_ref (detailed only)."""


def assess_base(
    repo_path: str, base: str, local_ref: str = "HEAD", *, detailed: bool = False
) -> BaseVerdict:
    """Fetch `base` (one retry) and assess `local_ref` against `origin/<base>`.

    `local_ref` is HEAD for the poller and the pushed SHA for the hook.
    `detailed` adds the per-commit list and local change set the push summary
    needs; it is off by default because incoming_commits costs one git call
    per commit. Never raises.
    """
    fetch_result = git_ops.fetch_with_retry(repo_path, base)
    if not fetch_result.ok:
        return BaseVerdict(base=base, fetch_ok=False, fetch_error=fetch_result.error)

    origin_ref = f"origin/{base}"
    head_sha = git_ops.resolve_ref(repo_path, origin_ref)

    behind, ahead = git_ops.behind_ahead(repo_path, local_ref, origin_ref)
    if behind == -1 and ahead == -1:
        return BaseVerdict(base=base, fetch_ok=True, head_sha=head_sha, comparison_ok=False)

    fields: dict = {
        "base": base,
        "fetch_ok": True,
        "head_sha": head_sha,
        "behind": behind,
        "ahead": ahead,
        "merge_conflict": False,
    }

    # Only a branch with commits of its own can conflict on committed work.
    if behind > 0 and ahead > 0:
        dry_run = git_ops.merge_tree_dry_run(repo_path, local_ref, origin_ref)
        if dry_run is None:
            fields["merge_conflict"] = None
        else:
            fields["merge_conflict"], fields["conflict_paths"] = dry_run

    if behind > 0 or detailed:
        mb = git_ops.merge_base(repo_path, local_ref, origin_ref)
        working_tree = git_ops.working_tree_paths(repo_path)
        branch_unique = git_ops.branch_unique_paths(repo_path, mb, local_ref) if mb else None
        incoming = git_ops.incoming_changed_paths(repo_path, mb, base) if mb else None

        fields["paths_determinable"] = None not in (mb, working_tree, branch_unique, incoming)
        if incoming is not None and working_tree is not None:
            fields["working_tree_overlap"] = sorted(incoming & working_tree)
        if fields["paths_determinable"]:
            fields["path_overlap"] = sorted(incoming & (working_tree | branch_unique))
        if detailed:
            fields["local_changes"] = (working_tree or set()) | (branch_unique or set())
            if mb:
                fields["incoming_commits"] = git_ops.incoming_commits(repo_path, mb, origin_ref)

    return BaseVerdict(**fields)


def _branch_status(repo_path: str, verdict: BaseVerdict) -> BranchStatus:
    """Map one verdict onto the menu's per-base status."""
    base = verdict.base

    def failed(reason: str) -> BranchStatus:
        return BranchStatus(
            base=base, behind=0, ahead_of_base=0, kind=StatusKind.CHECK_FAILED, reason=reason
        )

    def conflict_risk(paths: list[str]) -> BranchStatus:
        return BranchStatus(
            base=base,
            behind=verdict.behind,
            ahead_of_base=verdict.ahead,
            kind=StatusKind.CONFLICT_RISK,
            conflict_paths=paths,
            head_sha=verdict.head_sha,
        )

    if not verdict.fetch_ok:
        git_ops.log_fetch_failure(repo_path, base, verdict.fetch_error)
        return failed(git_ops.fetch_failure_reason(verdict.fetch_error))
    if not verdict.comparison_ok:
        return failed(_REASON_STATUS_FAILED)

    if verdict.behind > 0:
        committed_conflict = verdict.merge_conflict is True
        if committed_conflict or verdict.working_tree_overlap:
            paths = set(verdict.working_tree_overlap)
            if committed_conflict:
                paths |= set(verdict.conflict_paths)
            return conflict_risk(sorted(paths))
        if not verdict.paths_determinable:
            return failed(_REASON_CONFLICT_CHECK_FAILED)
        # merge-tree undeterminable: fall back to the path-overlap heuristic.
        if verdict.merge_conflict is None and verdict.path_overlap:
            return conflict_risk(verdict.path_overlap)

    if verdict.behind > 0 and verdict.ahead > 0:
        kind = StatusKind.DIVERGED
    elif verdict.behind > 0:
        kind = StatusKind.BEHIND
    else:
        kind = StatusKind.UP_TO_DATE
    return BranchStatus(
        base=base,
        behind=verdict.behind,
        ahead_of_base=verdict.ahead,
        kind=kind,
        head_sha=verdict.head_sha,
    )


def check_repo(repo: RepoConfig) -> RepoStatus:
    """Full status: every configured base branch + repo-level unpushed count.

    `unpushed` is computed once per repo (not per base). Each base gets its
    own verdict; a base that cannot be checked becomes a distinct CHECK_FAILED
    BranchStatus and checking continues for the repo's other bases rather than
    failing the whole repo. Never raises.
    """
    name = os.path.basename(repo.repo_path.rstrip("/"))

    def failed_repo(reason: str) -> RepoStatus:
        return RepoStatus(
            repo_path=repo.repo_path,
            name=name,
            current_branch=None,
            unpushed=0,
            branch_statuses=[],
            failure_reason=reason,
        )

    if not os.path.isdir(os.path.join(repo.repo_path, ".git")):
        return failed_repo(_REASON_REPO_MISSING)
    if not repo.base_branches:
        return failed_repo(_REASON_NO_BASES)

    return RepoStatus(
        repo_path=repo.repo_path,
        name=name,
        current_branch=git_ops.current_branch(repo.repo_path),
        unpushed=git_ops.unpushed_count(repo.repo_path),
        branch_statuses=[
            _branch_status(repo.repo_path, assess_base(repo.repo_path, base))
            for base in repo.base_branches
        ],
        failure_reason=None,
    )

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
import threading
from dataclasses import dataclass, field

from base_branch_watch.core import git_ops, log
from base_branch_watch.core.git_ops import Clean, Conflicted, MergeCheck, TimedOut, Undeterminable
from base_branch_watch.core.models import (
    DEFAULT_MERGE_TREE_TIMEOUT_SECONDS,
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
    merge_check: MergeCheck = field(default_factory=Undeterminable)
    """Committed work only: what merge-tree said. Undeterminable also covers
    "never asked" (fetch failed, comparison failed); TimedOut is its own case."""
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

    @property
    def merge_conflict(self) -> bool | None:
        """True/False from merge-tree, None = undeterminable (incl. timeout)."""
        if isinstance(self.merge_check, Clean):
            return False
        if isinstance(self.merge_check, Conflicted):
            return True
        return None

    @property
    def conflict_paths(self) -> list[str]:
        """Paths merge-tree reports as conflicted (git's own order)."""
        if isinstance(self.merge_check, Conflicted):
            return list(self.merge_check.paths)
        return []


def assess_base(
    repo_path: str,
    base: str,
    local_ref: str = "HEAD",
    *,
    detailed: bool = False,
    merge_tree_timeout: int = DEFAULT_MERGE_TREE_TIMEOUT_SECONDS,
) -> BaseVerdict:
    """Fetch `base` (one retry) and assess `local_ref` against `origin/<base>`.

    `local_ref` is HEAD for the poller and the pushed SHA for the hook.
    `detailed` adds the per-commit list and local change set the push summary
    needs; it is off by default because incoming_commits costs one git call
    per commit. Never raises.
    """
    fetch_result = git_ops.fetch_with_retry(repo_path, base)
    if not fetch_result.ok and not detailed:
        return BaseVerdict(base=base, fetch_ok=False, fetch_error=fetch_result.error)
    # A detailed (push-time) assessment of a failed fetch still reads the
    # stale origin ref so the summary can show what it knows; the verdict stays
    # fetch_ok=False with merge_conflict=None, so nothing treats it as fresh.
    fetched = fetch_result.ok

    origin_ref = f"origin/{base}"
    head_sha = git_ops.resolve_ref(repo_path, origin_ref) if fetched else None

    behind, ahead = git_ops.behind_ahead(repo_path, local_ref, origin_ref)
    if behind == -1 and ahead == -1:
        return BaseVerdict(
            base=base,
            fetch_ok=fetched,
            fetch_error=fetch_result.error,
            head_sha=head_sha,
            comparison_ok=False,
        )

    merge_check: MergeCheck = Clean()
    # Only a branch with commits of its own can conflict on committed work.
    if not fetched:
        merge_check = Undeterminable()  # stale origin ref proves nothing
    elif behind > 0 and ahead > 0:
        merge_check = git_ops.merge_tree_check(repo_path, local_ref, origin_ref, merge_tree_timeout)

    working_tree_overlap: list[str] = []
    path_overlap: list[str] = []
    paths_determinable = True
    local_changes: set[str] = set()
    commits: list[IncomingCommit] = []
    if behind > 0 or detailed:
        mb = git_ops.merge_base(repo_path, local_ref, origin_ref)
        working_tree = git_ops.working_tree_paths(repo_path)
        incoming = git_ops.incoming_changed_paths(repo_path, mb, base) if mb else None
        # Branch-unique paths only matter for the push summary and as the
        # fallback when merge-tree could not decide.
        need_branch_unique = detailed or not isinstance(merge_check, Clean | Conflicted)
        branch_unique = (
            git_ops.branch_unique_paths(repo_path, mb, local_ref)
            if mb and need_branch_unique
            else None
        )

        paths_determinable = (
            mb is not None
            and working_tree is not None
            and incoming is not None
            and (branch_unique is not None or not need_branch_unique)
        )
        if incoming is not None and working_tree is not None:
            working_tree_overlap = sorted(incoming & working_tree)
            if branch_unique is not None:
                path_overlap = sorted(incoming & (working_tree | branch_unique))
        if detailed:
            local_changes = (working_tree or set()) | (branch_unique or set())
            if mb:
                commits = git_ops.incoming_commits(repo_path, mb, origin_ref)

    return BaseVerdict(
        base=base,
        fetch_ok=fetched,
        fetch_error=fetch_result.error,
        head_sha=head_sha,
        behind=behind,
        ahead=ahead,
        merge_check=merge_check,
        working_tree_overlap=working_tree_overlap,
        path_overlap=path_overlap,
        paths_determinable=paths_determinable,
        local_changes=local_changes,
        incoming_commits=commits,
    )


_logged_merge_checks: dict[tuple[str, str], MergeCheck] = {}
_logged_merge_checks_lock = threading.Lock()


def _reset_logged_merge_checks() -> None:
    with _logged_merge_checks_lock:
        _logged_merge_checks.clear()


def _forget_logged_merge_check(repo_path: str, base: str) -> None:
    with _logged_merge_checks_lock:
        _logged_merge_checks.pop((repo_path, base), None)


def _forget_repo_logged_merge_checks(repo: RepoConfig) -> None:
    """An unchecked repo has no merge-check state: a recurrence after it comes back is new."""
    with _logged_merge_checks_lock:
        for key in [key for key in _logged_merge_checks if key[0] == repo.repo_path]:
            del _logged_merge_checks[key]


def prune_logged_merge_checks(repos: list[RepoConfig]) -> None:
    """Drop log-once memory for (repo, base) pairs no longer in the config, so a
    removed repo or base does not leak an entry for the life of the app."""
    live = {(repo.repo_path, base) for repo in repos for base in repo.base_branches}
    with _logged_merge_checks_lock:
        for key in [key for key in _logged_merge_checks if key not in live]:
            del _logged_merge_checks[key]


def _log_merge_check_once(repo_path: str, base: str, check: MergeCheck) -> None:
    """WARN when merge-tree timed out or failed, once per state change per
    (repo, base) rather than on every poll. A decisive answer resets the memory
    so a later recurrence is logged again. Menu path only: the pre-push hook
    shares assess_base and must not write the app log."""
    key = (repo_path, base)
    with _logged_merge_checks_lock:
        if isinstance(check, TimedOut):
            message = (
                f"[WARN] merge-tree timed out after {check.seconds}s in {repo_path} "
                f"(base {base}); raise merge_tree_timeout_seconds for this repo in config.json"
            )
        elif isinstance(check, Undeterminable) and check.cause:
            message = f"[WARN] merge-tree could not check {repo_path} (base {base}): {check.cause}"
        else:
            # Clean/Conflicted are decisive; an Undeterminable with no cause carries
            # nothing to log. Either way a later TimedOut is a recurrence: log it.
            _logged_merge_checks.pop(key, None)
            return
        if _logged_merge_checks.get(key) == check:
            return
        _logged_merge_checks[key] = check
    log.append(message)


def _branch_status(repo_path: str, verdict: BaseVerdict) -> BranchStatus:
    """Map one verdict onto the menu's per-base status."""
    base = verdict.base

    def failed(reason: str) -> BranchStatus:
        return BranchStatus(
            base=base, behind=0, ahead_of_base=0, kind=StatusKind.CHECK_FAILED, reason=reason
        )

    if not verdict.fetch_ok:
        _forget_logged_merge_check(repo_path, base)
        git_ops.log_fetch_failure(repo_path, base, verdict.fetch_error)
        return failed(git_ops.fetch_failure_reason(verdict.fetch_error))
    if not verdict.comparison_ok:
        _forget_logged_merge_check(repo_path, base)
        return failed(_REASON_STATUS_FAILED)

    _log_merge_check_once(repo_path, base, verdict.merge_check)
    # A timed-out check must stay visible whatever status the fallback picks.
    note = (
        git_ops.timed_out_text(verdict.merge_check.seconds)
        if isinstance(verdict.merge_check, TimedOut)
        else None
    )

    def conflict_risk(paths: list[str]) -> BranchStatus:
        return BranchStatus(
            base=base,
            behind=verdict.behind,
            ahead_of_base=verdict.ahead,
            kind=StatusKind.CONFLICT_RISK,
            reason=note,
            conflict_paths=paths,
            head_sha=verdict.head_sha,
        )

    if verdict.behind > 0:
        committed_conflict = verdict.merge_conflict is True
        if committed_conflict or verdict.working_tree_overlap:
            paths = set(verdict.working_tree_overlap)
            if committed_conflict:
                paths |= set(verdict.conflict_paths)
            return conflict_risk(sorted(paths))
        if not verdict.paths_determinable:
            return failed(note or _REASON_CONFLICT_CHECK_FAILED)
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
        reason=note,
        head_sha=verdict.head_sha,
    )


def check_repo(repo: RepoConfig) -> RepoStatus:
    """Full status: every configured base branch + repo-level unpushed count.

    `unpushed` is computed once per repo (not per base). Each base gets its
    own verdict; a base that cannot be checked becomes a distinct CHECK_FAILED
    BranchStatus and checking continues for the repo's other bases rather than
    failing the whole repo. Never raises.
    """
    if not os.path.isdir(os.path.join(repo.repo_path, ".git")):
        _forget_repo_logged_merge_checks(repo)
        return RepoStatus.failed(repo, _REASON_REPO_MISSING)
    if not repo.base_branches:
        _forget_repo_logged_merge_checks(repo)
        return RepoStatus.failed(repo, _REASON_NO_BASES)

    return RepoStatus(
        repo_path=repo.repo_path,
        name=os.path.basename(repo.repo_path.rstrip("/")),
        current_branch=git_ops.current_branch(repo.repo_path),
        unpushed=git_ops.unpushed_count(repo.repo_path),
        branch_statuses=[
            _branch_status(
                repo.repo_path,
                assess_base(
                    repo.repo_path, base, merge_tree_timeout=repo.merge_tree_timeout_seconds
                ),
            )
            for base in repo.base_branches
        ],
        failure_reason=None,
    )

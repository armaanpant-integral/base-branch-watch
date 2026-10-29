"""One refresh cycle's decisions, with no rumps/AppKit and no I/O of its own.

`PollCycle.run` takes the watched repos, the persisted notify state and the
monotonic clock, and returns everything the app needs to act: fresh git and PR
statuses, which statuses to put in the digest, the next notify state and the
log lines. The app logs, sends, saves and renders. The cycle owns only its own
memory (when each repo's PR was last checked, the last PR status per repo) and
takes every collaborator in its constructor so tests need no patching.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from base_branch_watch.core import git_ops, notify_policy, pr_status, status_text
from base_branch_watch.core.models import (
    PrStatus,
    PrStatusKind,
    RepoConfig,
    RepoStatus,
    StaleBranch,
    StatusKind,
)
from base_branch_watch.core.state import State
from base_branch_watch.runner import batch

# D-13's ~2min PR-check floor: in-memory only, resets on restart (a handful of
# idempotent read-only gh calls).
PR_CHECK_FLOOR_SECONDS = 120

_TERMINAL_PR_KINDS = (PrStatusKind.MERGED, PrStatusKind.CLOSED)


@dataclass(frozen=True)
class CycleResult:
    statuses: list[RepoStatus]
    pr_statuses: dict[str, PrStatus]
    digest_statuses: list[RepoStatus] = field(default_factory=list)
    """Statuses for this cycle's digest notification (empty = send nothing)."""
    state: State = field(default_factory=dict)
    """Notify state to persist after sending; equal to the input if unchanged."""
    log_lines: list[str] = field(default_factory=list)
    stale_statuses: dict[str, list[StaleBranch]] = field(default_factory=dict)
    """Stale local branches per repo path. A repo missing here could not be checked."""


def status_log_line(status: RepoStatus) -> str:
    """One log line per repo; CHECK_FAILED is labelled FAIL, everything else OK."""
    if status.failure_reason is not None:
        return f"[FAIL] {status.name}: {status.failure_reason}"
    level = "FAIL" if status.worst_kind == StatusKind.CHECK_FAILED else "OK"
    return f"[{level}] {status.name}: {status_text.repo_summary(status)}"


class PollCycle:
    def __init__(
        self,
        check_all: Callable[..., tuple[list[RepoStatus], dict[str, PrStatus]]] = batch.check_all,
        final_state: Callable[[str, int], PrStatusKind | None] = pr_status.final_state,
        current_branch: Callable[[str], str | None] = git_ops.current_branch,
        check_stale: Callable[
            [list[RepoConfig]], dict[str, list[StaleBranch]]
        ] = batch.check_stale_all,
    ) -> None:
        self._check_all = check_all
        self._final_state = final_state
        self._current_branch = current_branch
        self._check_stale = check_stale
        self.pr_statuses: dict[str, PrStatus] = {}
        self.last_pr_checked_at: dict[str, float] = {}

    def forget(self, repo_paths: list[str]) -> None:
        """Drop PR memory for repos that are no longer watched."""
        for repo_path in repo_paths:
            self.pr_statuses.pop(repo_path, None)
            self.last_pr_checked_at.pop(repo_path, None)

    def run(self, repos: list[RepoConfig], notify_state: State, now: float) -> CycleResult:
        pr_eligible = {
            repo.repo_path
            for repo in repos
            # WR-03: float("-inf") sentinel, not 0.0 - time.monotonic()'s
            # reference point is unspecified (effectively system uptime on
            # macOS/CPython), so a machine rebooted <2min before launch would
            # otherwise have every repo incorrectly excluded from this very
            # first cycle's eligible set.
            if now - self.last_pr_checked_at.get(repo.repo_path, float("-inf"))
            >= PR_CHECK_FLOOR_SECONDS
        }

        statuses, fresh_pr_statuses = self._check_all(repos, pr_repo_paths=pr_eligible)
        for repo_path in pr_eligible:
            self.last_pr_checked_at[repo_path] = now
        self.pr_statuses = self._merge_pr_statuses(statuses, fresh_pr_statuses)

        log_lines = [status_log_line(status) for status in statuses]
        try:
            stale_statuses = self._check_stale(repos)
        except Exception as exc:  # noqa: BLE001 - stale info is optional; never break the cycle
            stale_statuses = {}
            log_lines.append(f"[FAIL] stale branch check failed: {exc!r}")

        digest = notify_policy.decide_digest(notify_state, statuses)
        return CycleResult(
            statuses=statuses,
            pr_statuses=dict(self.pr_statuses),
            digest_statuses=digest.statuses,
            state=digest.state,
            log_lines=log_lines,
            stale_statuses=stale_statuses,
        )

    def _merge_pr_statuses(
        self, statuses: list[RepoStatus], fresh_pr_statuses: dict[str, PrStatus]
    ) -> dict[str, PrStatus]:
        """D-13 retention + D-03 one-cycle MERGED/CLOSED transition.

        For each currently-watched repo: if this cycle fetched a fresh
        PrStatus (repo was PR-eligible), use it, subject to two one-cycle
        collapse routes so a terminal PR only shows MERGED/CLOSED once
        (D-03) rather than indefinitely:

        Route A - `gh pr view` stops finding the PR entirely (its branch
        was deleted after merge/close): a fresh NO_PR immediately following
        a stored OPEN with a PR number probes final_state once; a
        MERGED/CLOSED result renders for this one cycle only (the NEXT
        cycle's stored value is already NO_PR, so it naturally falls back).

        Route B - `gh pr view` KEEPS finding the PR and reports it
        MERGED/CLOSED directly (branch still exists on GitHub after merge -
        CR-01, verified live: this is the common case, not the exception).
        First cycle: show the fresh MERGED/CLOSED. If the STORED value was
        already the same MERGED/CLOSED PR (i.e. this is the second+ cycle
        seeing the same terminal PR), collapse to NO_PR instead so the
        one-cycle confirmation doesn't linger.

        If this cycle was NOT PR-eligible (repo absent from
        fresh_pr_statuses), retain the prior stored value unchanged (no
        stale-data indicator, per 04-UI-SPEC.md's polling note).
        """
        merged: dict[str, PrStatus] = {}
        for status in statuses:
            repo_path = status.repo_path
            prev = self.pr_statuses.get(repo_path)
            if repo_path not in fresh_pr_statuses:
                if prev is not None:
                    merged[repo_path] = prev
                continue

            fresh = fresh_pr_statuses[repo_path]

            # Route A: branch disappeared from gh's lookup entirely.
            if (
                fresh.kind == PrStatusKind.NO_PR
                and prev is not None
                and prev.kind == PrStatusKind.OPEN
                and prev.number is not None
            ):
                probed_kind = self._final_state(repo_path, prev.number)
                if probed_kind in _TERMINAL_PR_KINDS:
                    merged[repo_path] = PrStatus(kind=probed_kind, number=prev.number)
                    continue

            # Route B: branch still exists; gh keeps reporting the same
            # terminal PR. Collapse to NO_PR after the first confirmation.
            if fresh.kind in _TERMINAL_PR_KINDS:
                if prev is not None and prev.kind == fresh.kind and prev.number == fresh.number:
                    merged[repo_path] = PrStatus(
                        kind=PrStatusKind.NO_PR,
                        current_branch=self._current_branch(repo_path),
                    )
                else:
                    merged[repo_path] = fresh
                continue

            merged[repo_path] = fresh
        return merged

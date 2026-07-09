"""Dataclasses and enums shared by core and app layers.

No rumps/AppKit import here, ever (core/ is UI-free per ARCH-01).
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from enum import IntEnum


class Severity(IntEnum):
    """Worst-status-wins ordering: OK < ATTENTION < BLOCKING."""

    OK = 0
    ATTENTION = 1
    BLOCKING = 2


class StatusKind(IntEnum):
    """All possible per-branch/per-repo status kinds.

    CONFLICT_RISK is RESERVED for Phase 2 (CONFLICT-01) — declared now so
    Phase 2 only adds a condition that sets it, not a redesign of this enum.
    Skeleton (Phase 1 Plan 01) scope only ever produces UP_TO_DATE, BEHIND,
    CHECK_FAILED, and NOT_CHECKED.
    """

    UP_TO_DATE = 0
    BEHIND = 1
    UNPUSHED = 2
    BEHIND_AND_UNPUSHED = 3
    DIVERGED = 4
    CONFLICT_RISK = 5  # reserved — nothing sets this in Phase 1
    CHECK_FAILED = 6
    NOT_CHECKED = 7


# StatusKind -> Severity mapping (worst-wins ordering source of truth).
_KIND_SEVERITY = {
    StatusKind.UP_TO_DATE: Severity.OK,
    StatusKind.NOT_CHECKED: Severity.OK,
    StatusKind.BEHIND: Severity.ATTENTION,
    StatusKind.UNPUSHED: Severity.ATTENTION,
    StatusKind.BEHIND_AND_UNPUSHED: Severity.ATTENTION,
    StatusKind.DIVERGED: Severity.BLOCKING,
    StatusKind.CONFLICT_RISK: Severity.BLOCKING,
    StatusKind.CHECK_FAILED: Severity.BLOCKING,
}

# Worst-wins ranking used to fold multiple per-base BranchStatus.kind values
# down to a single "worst branch kind" for a repo. Deliberately NOT the same
# as StatusKind's raw enum ordinal (e.g. BEHIND=1 < UNPUSHED=2 as declared
# above) -- this table is the actual severity-ordering source of truth per
# 01-03-PLAN.md: UP_TO_DATE < UNPUSHED < BEHIND < BEHIND_AND_UNPUSHED <
# DIVERGED < CONFLICT_RISK(reserved) < CHECK_FAILED.
_WORST_KIND_RANK = {
    StatusKind.UP_TO_DATE: 0,
    StatusKind.UNPUSHED: 1,
    StatusKind.BEHIND: 2,
    StatusKind.BEHIND_AND_UNPUSHED: 3,
    StatusKind.DIVERGED: 4,
    StatusKind.CONFLICT_RISK: 5,
    StatusKind.CHECK_FAILED: 6,
    StatusKind.NOT_CHECKED: -1,
}


@dataclass
class RepoConfig:
    """A single watched repo: its filesystem path and base branch(es)."""

    repo_path: str
    base_branches: list[str] = field(default_factory=list)


@dataclass
class BranchStatus:
    """Status of one base branch for a repo."""

    base: str
    behind: int
    ahead_of_base: int
    kind: StatusKind
    reason: str | None = None
    """Set when `kind == CHECK_FAILED` (e.g. a per-base fetch failure) - the
    reason this specific base could not be checked, distinct from a repo-level
    `RepoStatus.failure_reason` (missing repo dir, no base branches configured)."""


@dataclass
class RepoStatus:
    """Aggregate status for a single watched repo across its base branch(es)."""

    repo_path: str
    name: str
    current_branch: str | None
    unpushed: int
    branch_statuses: list[BranchStatus] = field(default_factory=list)
    failure_reason: str | None = None

    @classmethod
    def failed(cls, repo: RepoConfig, reason: str) -> "RepoStatus":
        """Build a CHECK_FAILED RepoStatus for a repo whose check raised.

        Used by runner.batch.check_all to isolate a per-repo exception in the
        thread pool to a single failed status rather than killing the batch.
        """
        name = os.path.basename(repo.repo_path.rstrip("/"))
        return cls(
            repo_path=repo.repo_path,
            name=name,
            current_branch=None,
            unpushed=0,
            branch_statuses=[],
            failure_reason=reason,
        )

    @property
    def worst_kind(self) -> StatusKind:
        """Fold per-base `branch_statuses` kinds together with the repo-level
        `unpushed` axis into a single worst-wins StatusKind.

        `unpushed` (ahead of the current branch's own origin) is computed once
        per repo, not per base -- see `_WORST_KIND_RANK` for the tier order.
        A per-base DIVERGED/CHECK_FAILED/CONFLICT_RISK always wins outright
        (already 🔴-tier regardless of unpushed); a per-base BEHIND combines
        with a nonzero `unpushed` into BEHIND_AND_UNPUSHED; otherwise a
        nonzero `unpushed` alone yields UNPUSHED.
        """
        if self.failure_reason is not None:
            return StatusKind.CHECK_FAILED
        if not self.branch_statuses:
            return StatusKind.NOT_CHECKED

        branch_worst = max(
            (bs.kind for bs in self.branch_statuses), key=lambda k: _WORST_KIND_RANK[k]
        )
        if branch_worst in (StatusKind.CHECK_FAILED, StatusKind.DIVERGED, StatusKind.CONFLICT_RISK):
            return branch_worst
        if branch_worst == StatusKind.BEHIND:
            return StatusKind.BEHIND_AND_UNPUSHED if self.unpushed > 0 else StatusKind.BEHIND
        return StatusKind.UNPUSHED if self.unpushed > 0 else StatusKind.UP_TO_DATE

    @property
    def worst_branch_status(self) -> "BranchStatus | None":
        """The single BranchStatus with the worst-wins kind (same ranking as
        `worst_kind`), or None if this repo has no per-base statuses yet.
        Used by notify/osascript_notifier.py to build a short, single-line
        status for a repo that may have multiple configured base branches.
        """
        if not self.branch_statuses:
            return None
        return max(self.branch_statuses, key=lambda bs: _WORST_KIND_RANK[bs.kind])

    @property
    def severity(self) -> Severity:
        return _KIND_SEVERITY[self.worst_kind]


@dataclass
class MenuItemSpec:
    """Pure, rumps-free description of a single menu row (possibly a submenu parent)."""

    title: str
    callback_key: str | None = None
    detail: str | None = None
    children: list["MenuItemSpec"] = field(default_factory=list)

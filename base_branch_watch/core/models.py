"""Dataclasses and enums shared by core and app layers.

No rumps/AppKit import here, ever (core/ is UI-free per ARCH-01).
"""

from __future__ import annotations

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


@dataclass
class RepoStatus:
    """Aggregate status for a single watched repo across its base branch(es)."""

    repo_path: str
    name: str
    current_branch: str | None
    unpushed: int
    branch_statuses: list[BranchStatus] = field(default_factory=list)
    failure_reason: str | None = None

    @property
    def worst_kind(self) -> StatusKind:
        if self.failure_reason is not None:
            return StatusKind.CHECK_FAILED
        if not self.branch_statuses:
            return StatusKind.NOT_CHECKED
        return max((bs.kind for bs in self.branch_statuses), key=lambda k: _KIND_SEVERITY[k])

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

"""Pure statuses -> dashboard panel view-model. No UI-toolkit imports.

One RepoTile per watched repo, sorted attention-first, plus the counts the
panel header and tabs show. PR and stale state are informational: they shape a
tile's pill and detail text but never its git-status severity.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from base_branch_watch.app import menu_builder
from base_branch_watch.core.models import (
    PrStatus,
    PrStatusKind,
    RepoStatus,
    Severity,
    StaleBranch,
    StatusKind,
)


class PillTone(Enum):
    OK = "ok"
    ATTENTION = "attention"
    NEUTRAL = "neutral"


@dataclass(frozen=True)
class RepoTile:
    repo_path: str
    name: str
    base: str
    behind: int
    unpushed: int
    severity: Severity
    pill: str
    pill_tone: PillTone
    detail: str


@dataclass(frozen=True)
class PanelModel:
    tiles: list[RepoTile]
    attention_count: int
    clear_count: int
    open_pr_count: int
    stale_total: int


def _git_pill(status: RepoStatus) -> tuple[str, PillTone] | None:
    """The pill for a git-level problem, or None when git state is fine."""
    kind = status.worst_kind
    if kind == StatusKind.CHECK_FAILED:
        return "Check failed", PillTone.ATTENTION
    if kind == StatusKind.CONFLICT_RISK:
        branch = status.worst_branch_status
        count = len(branch.conflict_paths) if branch is not None else 0
        return f"Conflict · {count} file{'' if count == 1 else 's'}", PillTone.ATTENTION
    if kind == StatusKind.DIVERGED:
        return "Diverged", PillTone.ATTENTION
    if kind in (StatusKind.BEHIND, StatusKind.BEHIND_AND_UNPUSHED):
        return "Behind base", PillTone.ATTENTION
    if kind == StatusKind.UNPUSHED:
        return "Unpushed", PillTone.ATTENTION
    if kind == StatusKind.NOT_CHECKED:
        return "Checking…", PillTone.NEUTRAL
    return None


def _pr_pill(pr_status: PrStatus | None) -> tuple[str, PillTone]:
    if pr_status is None or pr_status.kind == PrStatusKind.NO_PR:
        return "No open PR", PillTone.NEUTRAL
    if pr_status.kind == PrStatusKind.OPEN:
        glyph, headline = menu_builder.pr_headline(pr_status)
        if glyph == "✅":
            return "PR ready", PillTone.OK
        return headline, PillTone.ATTENTION
    if pr_status.kind == PrStatusKind.MERGED:
        return "PR merged", PillTone.NEUTRAL
    if pr_status.kind == PrStatusKind.CLOSED:
        return "PR closed", PillTone.NEUTRAL
    return "PR status unavailable", PillTone.NEUTRAL


def _detail(pr_status: PrStatus | None, stale_count: int) -> str:
    if pr_status is not None and pr_status.number is not None:
        return f"PR #{pr_status.number}"
    if stale_count > 0:
        return f"{stale_count} stale"
    return ""


def _tile(status: RepoStatus, pr_status: PrStatus | None, stale: list[StaleBranch]) -> RepoTile:
    git_pill = _git_pill(status)
    pill, tone = git_pill if git_pill is not None else _pr_pill(pr_status)
    branch = status.worst_branch_status
    return RepoTile(
        repo_path=status.repo_path,
        name=status.name,
        base=branch.base if branch is not None else "",
        behind=max((bs.behind for bs in status.branch_statuses), default=0),
        unpushed=status.unpushed,
        severity=status.severity,
        pill=pill,
        pill_tone=tone,
        detail=_detail(pr_status, len(stale)),
    )


def build_panel(
    statuses: list[RepoStatus],
    pr_statuses: dict[str, PrStatus],
    stale_statuses: dict[str, list[StaleBranch]],
) -> PanelModel:
    tiles = [
        _tile(status, pr_statuses.get(status.repo_path), stale_statuses.get(status.repo_path, []))
        for status in statuses
    ]
    tiles.sort(key=lambda tile: (-tile.severity, tile.name))
    attention = sum(1 for tile in tiles if tile.severity > Severity.OK)
    return PanelModel(
        tiles=tiles,
        attention_count=attention,
        clear_count=len(tiles) - attention,
        open_pr_count=sum(1 for pr in pr_statuses.values() if pr.kind == PrStatusKind.OPEN),
        stale_total=sum(len(branches) for branches in stale_statuses.values()),
    )

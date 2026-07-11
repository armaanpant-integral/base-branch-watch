"""Pure spec-building layer for the D-05/D-06 pre-push drift summary.

No terminal output happens here -- mirrors app/menu_builder.py's pure
statuses -> list[MenuItemSpec] precedent (core/ never talks to a terminal
or UI toolkit directly). hook.py owns all terminal output, rendering the
plain strings this module returns.
"""

from __future__ import annotations

from base_branch_watch.core.models import IncomingCommit

CAP = 15
"""D-05: cap the per-base commit list at this many lines before an
'…and N more' overflow tail, so a long-stale branch never floods push
output (mirrors app/menu_builder.py's CONFLICT_PATHS_ROW_CAP overflow shape)."""


def _commit_line(commit: IncomingCommit, overlap_paths: set[str]) -> str:
    """D-05's `"<short_hash> <author>: <subject>"` shape, D-06's leading
    warning marker when this commit's changed_paths intersect the fresh
    push-time overlap set (D-07)."""
    flag = "⚠️ " if commit.changed_paths & overlap_paths else ""
    return f"{flag}{commit.short_hash} {commit.author}: {commit.subject}"


def build_summary(
    per_base: list[tuple[str, list[IncomingCommit]]],
    overlap_paths: set[str],
    cap: int = CAP,
) -> list[str]:
    """D-05/D-06/D-07: one grouped, capped, overlap-flagged section per base.

    Pure -- returns lines only, no side effects; hook.py renders them to the
    terminal before the gate decision so the summary always shows, gated or
    not. `per_base` is `[(base_label, commits), ...]` (base_label is a
    caller-formatted string, e.g. `"origin/main"`); repeated per configured
    base for the multi-base grouping CONTEXT.md's Claude's-Discretion note
    calls for.
    """
    lines: list[str] = []
    for base, commits in per_base:
        lines.append(f"{base}: {len(commits)} incoming commit(s)")
        for commit in commits[:cap]:
            lines.append(_commit_line(commit, overlap_paths))
        overflow = len(commits) - cap
        if overflow > 0:
            lines.append(f"…and {overflow} more")
    return lines

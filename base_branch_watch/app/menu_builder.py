"""Pure statuses -> list[MenuItemSpec] builder + title_for(). No rumps import.

Full three-tier vocabulary (01-UI-SPEC.md): behind / unpushed / behind+unpushed /
diverged / check-failed / not-checked, rendered as a flat clickable row for a
single configured base branch, or a submenu (parent + one child per base) once
a repo has two or more base branches. `unpushed` is a repo-level axis (computed
once by core.git_ops, never per base) and is never duplicated into child rows.
"""

from __future__ import annotations

from base_branch_watch.core.models import (
    BranchStatus,
    MenuItemSpec,
    PrStatus,
    PrStatusKind,
    RepoStatus,
    Severity,
    StatusKind,
)

EMPTY_STATE_TITLE = "No repos watched — click Add Repo… below"

CONFLICT_PATHS_ROW_CAP = 10

PR_BRANCH_NAME_CAP = 40
PR_REASON_CAP = 50

_SEVERITY_GLYPH = {
    Severity.OK: "🟢",
    Severity.ATTENTION: "🟡",
    Severity.BLOCKING: "🔴",
}


def _conflict_path_children(bs: BranchStatus) -> list[MenuItemSpec]:
    """D-04: one child row per overlapping path, capped at
    CONFLICT_PATHS_ROW_CAP with an "…and N more" overflow row so a large
    overlap never renders an unusable menu (RESEARCH Open Question 2).
    bs.conflict_paths is already sorted by check_repo(); iteration order is
    preserved here."""
    children = [
        MenuItemSpec(title=path, callback_key=None)
        for path in bs.conflict_paths[:CONFLICT_PATHS_ROW_CAP]
    ]
    overflow = len(bs.conflict_paths) - CONFLICT_PATHS_ROW_CAP
    if overflow > 0:
        children.append(MenuItemSpec(title=f"…and {overflow} more", callback_key=None))
    return children


def _check_failed_reason(status: RepoStatus, bs: BranchStatus) -> str:
    """Repo-level failure_reason (missing dir, no bases configured) takes
    precedence; otherwise fall back to this specific base's own reason
    (per-base fetch failure)."""
    return status.failure_reason or bs.reason or "unknown error"


def _single_base_row(status: RepoStatus) -> MenuItemSpec:
    bs = status.branch_statuses[0]
    unpushed = status.unpushed

    if bs.kind == StatusKind.CHECK_FAILED:
        reason = _check_failed_reason(status, bs)
        return MenuItemSpec(
            title=f"🔴 {status.name}: check failed — {reason}",
            callback_key=status.repo_path,
        )

    if bs.kind == StatusKind.DIVERGED:
        return MenuItemSpec(
            title=(
                f"🔴 {status.name}: diverged — {bs.behind} behind, "
                f"{bs.ahead_of_base} ahead ({bs.base})"
            ),
            callback_key=status.repo_path,
        )

    if bs.kind == StatusKind.CONFLICT_RISK:
        return MenuItemSpec(
            title=(
                f"⚠️ {status.name}: conflict risk — "
                f"{len(bs.conflict_paths)} file(s) overlap ({bs.base})"
            ),
            callback_key=None,
            children=_conflict_path_children(bs),
        )

    if bs.kind == StatusKind.BEHIND:
        if unpushed > 0:
            title = f"🟡 {status.name}: {bs.behind} behind ({bs.base}) · {unpushed} unpushed"
        else:
            title = f"🟡 {status.name}: {bs.behind} behind ({bs.base})"
        return MenuItemSpec(title=title, callback_key=status.repo_path)

    # UP_TO_DATE at the branch level - unpushed alone still needs attention.
    if unpushed > 0:
        return MenuItemSpec(
            title=f"🟡 {status.name}: {unpushed} unpushed", callback_key=status.repo_path
        )
    return MenuItemSpec(title=f"🟢 {status.name}", callback_key=status.repo_path)


def _child_row(status: RepoStatus, bs: BranchStatus) -> MenuItemSpec:
    """Same vocabulary as the single-base table, minus repo name and unpushed
    (unpushed is a repo-level axis, never duplicated per base)."""
    if bs.kind == StatusKind.CHECK_FAILED:
        reason = bs.reason or "unknown error"
        title = f"🔴 {bs.base}: check failed — {reason}"
    elif bs.kind == StatusKind.DIVERGED:
        title = f"🔴 {bs.base}: diverged — {bs.behind} behind, {bs.ahead_of_base} ahead"
    elif bs.kind == StatusKind.CONFLICT_RISK:
        title = f"⚠️ {bs.base}: conflict risk — {len(bs.conflict_paths)} file(s) overlap"
    elif bs.kind == StatusKind.BEHIND:
        title = f"🟡 {bs.base}: {bs.behind} behind"
    else:
        title = f"🟢 {bs.base}"
    is_conflict_risk = bs.kind == StatusKind.CONFLICT_RISK
    children = _conflict_path_children(bs) if is_conflict_risk else []
    callback_key = None if is_conflict_risk else f"{status.repo_path}::{bs.base}"
    return MenuItemSpec(title=title, callback_key=callback_key, children=children)


def _multi_base_row(status: RepoStatus) -> MenuItemSpec:
    if status.worst_kind == StatusKind.CONFLICT_RISK:
        glyph = "⚠️"
    else:
        glyph = _SEVERITY_GLYPH[status.severity]
    title = f"{glyph} {status.name} ({len(status.branch_statuses)} base branches)"
    if status.unpushed > 0:
        title += f" · {status.unpushed} unpushed"
    children = [_child_row(status, bs) for bs in status.branch_statuses]
    # Parent has NO callback - a native NSMenu submenu parent only expands;
    # attaching a callback would conflict with that.
    return MenuItemSpec(title=title, callback_key=None, children=children)


def _row_for(status: RepoStatus) -> MenuItemSpec:
    if not status.branch_statuses:
        if status.failure_reason is not None:
            return MenuItemSpec(
                title=f"🔴 {status.name}: check failed — {status.failure_reason}",
                callback_key=status.repo_path,
            )
        return MenuItemSpec(title=f"… {status.name}", callback_key=None)

    if len(status.branch_statuses) == 1:
        return _single_base_row(status)

    return _multi_base_row(status)


def build(statuses: list[RepoStatus], has_repos: bool) -> list[MenuItemSpec]:
    if not has_repos:
        return [MenuItemSpec(title=EMPTY_STATE_TITLE, callback_key=None)]
    return [_row_for(status) for status in statuses]


def _truncate(text: str, cap: int) -> str:
    """Cap `text` at `cap` chars, appending a trailing … if longer (result
    length is at most cap + 1, matching the 04-UI-SPEC.md truncation rules)."""
    if len(text) <= cap:
        return text
    return text[:cap] + "…"


def _checks_segment(status: PrStatus) -> tuple[str, str, str]:
    """(glyph, top-level text, submenu child text) for the Checks segment —
    04-UI-SPEC.md Checks segment + Submenu Children tables."""
    total = status.checks_total
    passed = status.checks_pass
    fail = status.checks_fail
    pending = status.checks_pending
    if total == 0:
        return "—", "no checks", "no checks configured"
    if fail > 0:
        text = f"{fail} failing ({passed}/{total})"
        return "❌", text, text
    if pending > 0:
        text = f"{pending} pending ({passed}/{total})"
        return "⏳", text, text
    return "✅", f"{passed}/{total} checks", f"{passed}/{total} passing"


def _review_segment(status: PrStatus) -> tuple[str, str, str]:
    """(glyph, top-level text, submenu child text) for the Review segment —
    04-UI-SPEC.md Review segment + Submenu Children tables."""
    decision = status.review_decision
    if decision == "APPROVED":
        return "✅", "approved", "approved"
    if decision == "CHANGES_REQUESTED":
        return "❌", "changes requested", "changes requested"
    if decision == "REVIEW_REQUIRED":
        return "⏳", "review pending", "pending"
    return "—", "no review required", "not required"


def _mergeable_segment(status: PrStatus) -> tuple[str, str, str]:
    """(glyph, top-level text, submenu child text) for the Mergeable segment —
    04-UI-SPEC.md Mergeable segment + Submenu Children tables. Every known
    mergeStateStatus value has an explicit branch; any unlisted/future value
    falls into the forward-compatible default (never a raise/crash, per
    Common Pitfall 3)."""
    value = status.merge_state_status
    base = status.base_ref
    if value in ("CLEAN", "HAS_HOOKS"):
        return "✅", "mergeable", "yes"
    if value == "DIRTY":
        child = f"conflicts with {base}" if base else "conflicts"
        return "❌", "conflicts", child
    if value == "BLOCKED":
        return "❌", "blocked (required checks)", "blocked — required checks not met"
    if value == "BEHIND":
        child = f"behind {base}" if base else "behind base"
        return "⏳", "behind base", child
    if value == "DRAFT":
        return "⏳", "draft", "draft PR"
    if value == "UNSTABLE":
        return "⏳", "mergeable (optional check failing)", "yes (optional check failing)"
    return "⏳", "mergeability unknown", "unknown — GitHub still computing"


def _pr_row(pr_status: PrStatus, repo_name: str) -> MenuItemSpec:
    """D-05/D-06/D-07/D-02 — the second, independent PR-status row per repo.

    Never uses 🟢/🟡/🔴 (D-08) — a completely separate glyph vocabulary from
    _row_for's git-status rows. This phase (Plan 01) only ever receives OPEN,
    NO_PR, or CHECK_FAILED from core.pr_status.check_pr; any other kind falls
    into the CHECK_FAILED-shaped fallback below so a future kind never raises.
    """
    if pr_status.kind == PrStatusKind.OPEN:
        checks_glyph, checks_text, checks_child = _checks_segment(pr_status)
        review_glyph, review_text, review_child = _review_segment(pr_status)
        mergeable_glyph, mergeable_text, mergeable_child = _mergeable_segment(pr_status)
        title = (
            f"🔀 {repo_name}: PR #{pr_status.number} — "
            f"{checks_glyph} {checks_text} · {review_glyph} {review_text} · "
            f"{mergeable_glyph} {mergeable_text}"
        )
        children = [
            MenuItemSpec(title=f"{checks_glyph} Checks: {checks_child}", callback_key=None),
            MenuItemSpec(title=f"{review_glyph} Review: {review_child}", callback_key=None),
            MenuItemSpec(
                title=f"{mergeable_glyph} Mergeable: {mergeable_child}", callback_key=None
            ),
        ]
        return MenuItemSpec(title=title, callback_key=None, children=children)

    if pr_status.kind == PrStatusKind.NO_PR:
        branch = _truncate(pr_status.current_branch or "", PR_BRANCH_NAME_CAP)
        return MenuItemSpec(title=f"⚪ {repo_name}: no open PR ({branch})", callback_key=None)

    # CHECK_FAILED (generic Plan-01 scope) and forward-compat fallback for any
    # other kind — never a raise/crash (D-11).
    reason = _truncate(pr_status.reason or "unknown error", PR_REASON_CAP)
    return MenuItemSpec(
        title=f"⚠️ {repo_name}: PR status unavailable — {reason}", callback_key=None
    )


def title_for(statuses: list[RepoStatus]) -> str:
    if not statuses:
        return _SEVERITY_GLYPH[Severity.OK]

    worst = max((s.severity for s in statuses), default=Severity.OK)
    count = sum(1 for s in statuses if s.severity >= worst and worst != Severity.OK)

    if worst == Severity.OK:
        return _SEVERITY_GLYPH[Severity.OK]
    return f"{_SEVERITY_GLYPH[worst]} {count}"

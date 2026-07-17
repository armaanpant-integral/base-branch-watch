"""All `gh` subprocess invocation lives here. No rumps/AppKit import, ever.

Every subprocess.run call passes an explicit timeout= and an arg-list only,
no shell string interpolation — mirrors core/git_ops.py's discipline exactly
(see CLAUDE.md "What NOT to Use").

Key deviation from git_ops.py's `_run_git`: `gh` has no `-C <path>` flag
(unlike git), so `cwd=repo_path` is used instead — RESEARCH.md Pitfall 1,
live-verified. `GH` also has NO fallback path (unlike `GIT`'s `or
"/usr/bin/git"`) — `gh` has no canonical fixed install location, and `GH is
None` is itself the NOT_INSTALLED sentinel trigger.
"""

from __future__ import annotations

import datetime
import json
import shutil
import subprocess

from base_branch_watch.core import git_ops
from base_branch_watch.core.models import PrStatus, PrStatusKind

GH = shutil.which("gh")

_VIEW_FIELDS = "number,state,mergeStateStatus,reviewDecision,baseRefName"


def _run_gh(repo_path: str, args: list[str], timeout: int) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [GH, *args],
        cwd=repo_path,  # NOT "-C repo_path" -- gh has no such flag
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def _checks_counts(repo_path: str, timeout: int) -> tuple[int, int, int, int]:
    """`gh pr checks --json bucket` aggregate — (pass, fail, pending, total).

    Only used to detect invocation-level failure; never trusts the exit code
    for pass/fail (RESEARCH Pitfall 2 — `gh pr checks --json ...` returns
    exit 0 even with failing checks). On any failure to invoke/parse, total
    is -1 — a distinct "unavailable" sentinel from "genuinely zero checks
    configured" (WR-04), so the caller can render "checks unavailable"
    instead of misleadingly claiming "no checks configured".
    """
    try:
        result = _run_gh(repo_path, ["pr", "checks", "--json", "bucket"], timeout)
    except (subprocess.TimeoutExpired, OSError):
        return (0, 0, 0, -1)
    if result.returncode != 0:
        # `gh pr checks` exits nonzero with empty stdout when a PR genuinely
        # has zero checks configured - not a fetch failure, so don't collapse
        # it into the -1 "unavailable" sentinel.
        if "no checks reported" in result.stderr.lower():
            return (0, 0, 0, 0)
        return (0, 0, 0, -1)
    try:
        buckets = json.loads(result.stdout)
    except json.JSONDecodeError:
        return (0, 0, 0, -1)
    if not isinstance(buckets, list):
        return (0, 0, 0, -1)
    checks_pass = sum(1 for b in buckets if isinstance(b, dict) and b.get("bucket") == "pass")
    checks_fail = sum(1 for b in buckets if isinstance(b, dict) and b.get("bucket") == "fail")
    checks_pending = sum(
        1 for b in buckets if isinstance(b, dict) and b.get("bucket") == "pending"
    )
    return (checks_pass, checks_fail, checks_pending, len(buckets))


def rate_limit_reset_text(timeout: int = 10) -> str | None:
    """`gh api rate_limit`'s graphql.reset epoch -> local "HH:MM" string.

    `gh pr view`/`gh pr checks` use the GraphQL endpoint (RESEARCH.md,
    verified live via a forced-bad-token 401 against the graphql URL), so
    the graphql resource — not core — is the relevant quota. Never raises;
    returns None on any failure (D-10's reset time is best-effort).
    """
    if GH is None:
        return None
    try:
        result = subprocess.run(
            [GH, "api", "rate_limit"], capture_output=True, text=True, timeout=timeout
        )
    except (subprocess.TimeoutExpired, OSError):
        return None
    if result.returncode != 0:
        return None
    try:
        data = json.loads(result.stdout)
        reset_epoch = data["resources"]["graphql"]["reset"]
    except (json.JSONDecodeError, KeyError):
        return None
    return datetime.datetime.fromtimestamp(reset_epoch).strftime("%H:%M")


def check_pr(repo_path: str, timeout: int = 15) -> PrStatus:
    """Fetch the current branch's PR state via `gh`. Never raises.

    Distinguishes OPEN / NO_PR / NOT_INSTALLED / NOT_AUTHENTICATED /
    RATE_LIMITED / CHECK_FAILED (D-09/D-10/D-11) — five distinct
    failure/absence sentinels, never a generic catch-all.
    """
    if GH is None:
        return PrStatus(kind=PrStatusKind.NOT_INSTALLED)

    try:
        result = _run_gh(repo_path, ["pr", "view", "--json", _VIEW_FIELDS], timeout)
    except subprocess.TimeoutExpired:
        return PrStatus.failed(f"gh pr view timed out after {timeout}s")
    except OSError as exc:
        return PrStatus.failed(str(exc))

    if result.returncode == 4:
        # Verified live (RESEARCH.md): auth-required gh commands exit 4.
        return PrStatus(kind=PrStatusKind.NOT_AUTHENTICATED)

    if result.returncode != 0:
        stderr = result.stderr.strip()
        if "no pull requests found" in stderr:
            return PrStatus(
                kind=PrStatusKind.NO_PR,
                current_branch=git_ops.current_branch(repo_path),
            )
        if "API rate limit" in stderr or "rate limit" in stderr.lower():
            return PrStatus(
                kind=PrStatusKind.RATE_LIMITED,
                retry_at=rate_limit_reset_text(),
                reason=stderr[:200],
            )
        return PrStatus.failed(stderr or "gh pr view failed")

    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError:
        return PrStatus.failed("gh pr view returned unparseable JSON")

    # CR-01: `gh pr view` (no PR number given) keeps resolving to the PR
    # associated with the current branch even after it merges/closes, as
    # long as that branch still exists on GitHub — it does NOT fall through
    # to "no pull requests found" (verified live). Detect this directly
    # instead of relying solely on final_state()'s NO_PR-transition probe.
    state = data.get("state")
    if state == "MERGED":
        return PrStatus(kind=PrStatusKind.MERGED, number=data.get("number"))
    if state == "CLOSED":
        return PrStatus(kind=PrStatusKind.CLOSED, number=data.get("number"))

    checks_pass, checks_fail, checks_pending, checks_total = _checks_counts(repo_path, timeout)

    return PrStatus(
        kind=PrStatusKind.OPEN,
        number=data.get("number"),
        checks_pass=checks_pass,
        checks_fail=checks_fail,
        checks_pending=checks_pending,
        checks_total=checks_total,
        review_decision=data.get("reviewDecision") or None,
        merge_state_status=data.get("mergeStateStatus"),
        base_ref=data.get("baseRefName"),
    )


def final_state(repo_path: str, number: int, timeout: int = 15) -> PrStatusKind:
    """D-03 — probe a specific PR's terminal state once it stops matching
    `check_pr`'s default open-branch lookup (an OPEN -> NO_PR transition).

    Never raises: any failure to invoke/parse, or a state other than MERGED/
    CLOSED, maps to NO_PR (the caller's natural fallback — this probe is
    only ever consulted for a one-cycle confirmation, never the primary
    state source).
    """
    if GH is None:
        return PrStatusKind.NO_PR
    try:
        result = _run_gh(repo_path, ["pr", "view", str(number), "--json", "state"], timeout)
    except (subprocess.TimeoutExpired, OSError):
        return PrStatusKind.NO_PR
    if result.returncode != 0:
        return PrStatusKind.NO_PR
    try:
        data = json.loads(result.stdout)
        state = data.get("state")
    except json.JSONDecodeError:
        return PrStatusKind.NO_PR
    if state == "MERGED":
        return PrStatusKind.MERGED
    if state == "CLOSED":
        return PrStatusKind.CLOSED
    return PrStatusKind.NO_PR

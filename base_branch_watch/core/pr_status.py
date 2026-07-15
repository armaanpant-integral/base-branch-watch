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

import json
import shutil
import subprocess

from base_branch_watch.core import git_ops
from base_branch_watch.core.models import PrStatus, PrStatusKind

GH = shutil.which("gh")

_VIEW_FIELDS = "number,state,mergeable,mergeStateStatus,reviewDecision,baseRefName"


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
    exit 0 even with failing checks). On any failure to invoke/parse, all
    four counts stay 0.
    """
    try:
        result = _run_gh(repo_path, ["pr", "checks", "--json", "bucket"], timeout)
    except (subprocess.TimeoutExpired, OSError):
        return (0, 0, 0, 0)
    try:
        buckets = json.loads(result.stdout)
    except json.JSONDecodeError:
        return (0, 0, 0, 0)
    if not isinstance(buckets, list):
        return (0, 0, 0, 0)
    checks_pass = sum(1 for b in buckets if b.get("bucket") == "pass")
    checks_fail = sum(1 for b in buckets if b.get("bucket") == "fail")
    checks_pending = sum(1 for b in buckets if b.get("bucket") == "pending")
    return (checks_pass, checks_fail, checks_pending, len(buckets))


def check_pr(repo_path: str, timeout: int = 15) -> PrStatus:
    """Fetch the current branch's PR state via `gh`. Never raises.

    Distinguishes OPEN / NO_PR / NOT_INSTALLED / CHECK_FAILED (D-09/D-11 —
    NOT_AUTHENTICATED/RATE_LIMITED refinement is Plan 02 scope; any other
    nonzero exit here falls into CHECK_FAILED for this plan).
    """
    if GH is None:
        return PrStatus(kind=PrStatusKind.NOT_INSTALLED)

    try:
        result = _run_gh(repo_path, ["pr", "view", "--json", _VIEW_FIELDS], timeout)
    except subprocess.TimeoutExpired:
        return PrStatus.failed(f"gh pr view timed out after {timeout}s")
    except OSError as exc:
        return PrStatus.failed(str(exc))

    if result.returncode != 0:
        stderr = result.stderr.strip()
        if "no pull requests found" in stderr:
            return PrStatus(
                kind=PrStatusKind.NO_PR,
                current_branch=git_ops.current_branch(repo_path),
            )
        return PrStatus.failed(stderr or "gh pr view failed")

    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError:
        return PrStatus.failed("gh pr view returned unparseable JSON")

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

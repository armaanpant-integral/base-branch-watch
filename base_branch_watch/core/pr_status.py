"""Turn `gh` output into a PrStatus. No rumps/AppKit import, ever.

`PrStatusReader` owns the parsing and classification (OPEN, NO_PR, RATE_LIMITED,
...); the actual `gh` process lives behind the `GhClient` seam (core/gh_client.py).
The reader takes its client and its current-branch lookup in the constructor, so
tests need no patching. The module-level `check_pr` / `final_state` /
are thin wrappers over a default reader for callers that do not inject one.
"""

from __future__ import annotations

import datetime
import json
from collections.abc import Callable

from base_branch_watch.core import git_ops
from base_branch_watch.core.gh_client import GhClient, SubprocessGhClient
from base_branch_watch.core.models import PrStatus, PrStatusKind

# Exact field list passed to `gh pr view --json`. check_pr() below reads each of
# these via data.get(...) -- adding a new field to PrStatus in models.py also
# requires adding it here, or gh will never return it.
_VIEW_FIELDS = "number,state,mergeStateStatus,reviewDecision,baseRefName"

_CHECKS_UNAVAILABLE = (0, 0, 0, -1)


class PrStatusReader:
    def __init__(
        self,
        client: GhClient,
        current_branch: Callable[[str], str | None] = git_ops.current_branch,
    ) -> None:
        self._client = client
        self._current_branch = current_branch

    def check_pr(self, repo_path: str, timeout: int = 15) -> PrStatus:
        """Fetch the current branch's PR state via `gh`. Never raises.

        Distinguishes OPEN / NO_PR / NOT_INSTALLED / NOT_AUTHENTICATED /
        RATE_LIMITED / CHECK_FAILED (D-09/D-10/D-11) - six distinct
        failure/absence kinds, never a generic catch-all.
        """
        if not self._client.installed:
            return PrStatus(kind=PrStatusKind.NOT_INSTALLED)

        view = self._client.run(repo_path, ["pr", "view", "--json", _VIEW_FIELDS], timeout)
        if view.error is not None:
            return PrStatus.failed(view.error)

        if view.returncode == 4:
            # Verified live (RESEARCH.md): auth-required gh commands exit 4.
            return PrStatus(kind=PrStatusKind.NOT_AUTHENTICATED)

        if view.returncode != 0:
            stderr = view.stderr.strip()
            if "no pull requests found" in stderr:
                return PrStatus(
                    kind=PrStatusKind.NO_PR,
                    current_branch=self._current_branch(repo_path),
                )
            if "rate limit" in stderr.lower():
                return PrStatus(
                    kind=PrStatusKind.RATE_LIMITED,
                    retry_at=self.rate_limit_reset_text(),
                    reason=stderr[:200],
                )
            return PrStatus.failed(stderr or "gh pr view failed")

        try:
            data = json.loads(view.stdout)
        except json.JSONDecodeError:
            return PrStatus.failed("gh pr view returned unparseable JSON")

        # CR-01: `gh pr view` (no PR number given) keeps resolving to the PR
        # associated with the current branch even after it merges/closes, as
        # long as that branch still exists on GitHub - it does NOT fall through
        # to "no pull requests found" (verified live). Detect this directly
        # instead of relying solely on final_state()'s NO_PR-transition probe.
        state = data.get("state")
        if state == "MERGED":
            return PrStatus(kind=PrStatusKind.MERGED, number=data.get("number"))
        if state == "CLOSED":
            return PrStatus(kind=PrStatusKind.CLOSED, number=data.get("number"))

        checks_pass, checks_fail, checks_pending, checks_total = self._checks_counts(
            repo_path, timeout
        )

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

    def final_state(self, repo_path: str, number: int, timeout: int = 15) -> PrStatusKind:
        """D-03 - probe a specific PR's terminal state once it stops matching
        `check_pr`'s default open-branch lookup (an OPEN -> NO_PR transition).

        Never raises: any failure to invoke/parse, or a state other than MERGED/
        CLOSED, maps to NO_PR (the caller's natural fallback - this probe is
        only ever consulted for a one-cycle confirmation, never the primary
        state source).
        """
        result = self._client.run(
            repo_path, ["pr", "view", str(number), "--json", "state"], timeout
        )
        if not result.ok:
            return PrStatusKind.NO_PR
        try:
            state = json.loads(result.stdout).get("state")
        except json.JSONDecodeError:
            return PrStatusKind.NO_PR
        if state == "MERGED":
            return PrStatusKind.MERGED
        if state == "CLOSED":
            return PrStatusKind.CLOSED
        return PrStatusKind.NO_PR

    def rate_limit_reset_text(self, timeout: int = 10) -> str | None:
        """`gh api rate_limit`'s graphql.reset epoch -> local "HH:MM" string.

        `gh pr view`/`gh pr checks` use the GraphQL endpoint (RESEARCH.md,
        verified live via a forced-bad-token 401 against the graphql URL), so
        the graphql resource - not core - is the relevant quota. Never raises;
        returns None on any failure (D-10's reset time is best-effort).
        """
        result = self._client.run(None, ["api", "rate_limit"], timeout)
        if not result.ok:
            return None
        try:
            reset_epoch = json.loads(result.stdout)["resources"]["graphql"]["reset"]
        except (json.JSONDecodeError, KeyError):
            return None
        return datetime.datetime.fromtimestamp(reset_epoch).strftime("%H:%M")

    def _checks_counts(self, repo_path: str, timeout: int) -> tuple[int, int, int, int]:
        """`gh pr checks --json bucket` aggregate - (pass, fail, pending, total).

        Only used to detect invocation-level failure; never trusts the exit code
        for pass/fail (RESEARCH Pitfall 2 - `gh pr checks --json ...` returns
        exit 0 even with failing checks). On any failure to invoke/parse, total
        is -1 - a distinct "unavailable" sentinel from "genuinely zero checks
        configured" (WR-04), so the caller can render "checks unavailable"
        instead of misleadingly claiming "no checks configured".
        """
        result = self._client.run(repo_path, ["pr", "checks", "--json", "bucket"], timeout)
        if result.error is not None:
            return _CHECKS_UNAVAILABLE
        if result.returncode != 0:
            # `gh pr checks` exits nonzero with empty stdout when a PR genuinely
            # has zero checks configured - not a fetch failure, so don't collapse
            # it into the -1 "unavailable" sentinel.
            if "no checks reported" in result.stderr.lower():
                return (0, 0, 0, 0)
            return _CHECKS_UNAVAILABLE
        try:
            buckets = json.loads(result.stdout)
        except json.JSONDecodeError:
            return _CHECKS_UNAVAILABLE
        if not isinstance(buckets, list):
            return _CHECKS_UNAVAILABLE
        names = [b.get("bucket") for b in buckets if isinstance(b, dict)]
        return (names.count("pass"), names.count("fail"), names.count("pending"), len(buckets))


_default_reader = PrStatusReader(SubprocessGhClient())


def check_pr(repo_path: str, timeout: int = 15) -> PrStatus:
    return _default_reader.check_pr(repo_path, timeout)


def final_state(repo_path: str, number: int, timeout: int = 15) -> PrStatusKind:
    return _default_reader.final_state(repo_path, number, timeout)

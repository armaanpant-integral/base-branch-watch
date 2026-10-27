"""status_text: the one place RepoStatus / BranchStatus become words.

Pure and table-driven - every surface (menu row, digest, log, alert) calls these.
"""

from __future__ import annotations

import pytest

from base_branch_watch.core import status_text
from base_branch_watch.core.models import BranchStatus, RepoStatus, StatusKind


def _bs(kind, behind=0, ahead=0, base="main", reason=None, conflict_paths=()):
    return BranchStatus(
        base=base,
        behind=behind,
        ahead_of_base=ahead,
        kind=kind,
        reason=reason,
        conflict_paths=list(conflict_paths),
    )


def _repo(*branch_statuses, unpushed=0, failure_reason=None):
    return RepoStatus(
        repo_path="/tmp/repo",
        name="repo",
        current_branch="feature",
        unpushed=unpushed,
        branch_statuses=list(branch_statuses),
        failure_reason=failure_reason,
    )


# -- base_summary --------------------------------------------------------------


@pytest.mark.parametrize(
    ("branch_status", "show_base", "expected"),
    [
        (_bs(StatusKind.UP_TO_DATE), True, "up to date"),
        (_bs(StatusKind.BEHIND, behind=3), True, "3 behind (main)"),
        (_bs(StatusKind.BEHIND, behind=3), False, "3 behind"),
        (_bs(StatusKind.DIVERGED, behind=3, ahead=2), True, "diverged - 3 behind, 2 ahead (main)"),
        (_bs(StatusKind.DIVERGED, behind=4, ahead=1), False, "diverged - 4 behind, 1 ahead"),
        (
            _bs(StatusKind.CONFLICT_RISK, behind=2, conflict_paths=["a.py", "b.py"]),
            True,
            "conflict risk - 2 file(s) overlap (main)",
        ),
        (
            _bs(StatusKind.CONFLICT_RISK, behind=2, conflict_paths=["a.py"]),
            False,
            "conflict risk - 1 file(s) overlap",
        ),
        (
            _bs(StatusKind.CHECK_FAILED, reason="fetch failed"),
            True,
            "check failed - fetch failed (main)",
        ),
        (_bs(StatusKind.CHECK_FAILED, reason="fetch failed"), False, "check failed - fetch failed"),
        (_bs(StatusKind.CHECK_FAILED), True, "check failed - unknown error (main)"),
    ],
)
def test_base_summary(branch_status, show_base, expected):
    assert status_text.base_summary(branch_status, show_base=show_base) == expected


# -- repo_summary --------------------------------------------------------------


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        (_repo(failure_reason="repo folder not found"), "check failed - repo folder not found"),
        (_repo(), "not checked yet"),
        (_repo(_bs(StatusKind.UP_TO_DATE)), "up to date"),
        (_repo(_bs(StatusKind.UP_TO_DATE), unpushed=2), "2 unpushed"),
        (_repo(_bs(StatusKind.BEHIND, behind=3)), "3 behind (main)"),
        (_repo(_bs(StatusKind.BEHIND, behind=3), unpushed=1), "3 behind (main) · 1 unpushed"),
        (
            _repo(_bs(StatusKind.DIVERGED, behind=3, ahead=2), unpushed=1),
            "diverged - 3 behind, 2 ahead (main)",
        ),
        (
            _repo(_bs(StatusKind.CONFLICT_RISK, behind=1, conflict_paths=["a.py"])),
            "conflict risk - 1 file(s) overlap (main)",
        ),
        (
            _repo(_bs(StatusKind.CHECK_FAILED, reason="no route")),
            "check failed - no route (main)",
        ),
        (
            _repo(_bs(StatusKind.CHECK_FAILED, reason="no route"), unpushed=2),
            "check failed - no route (main)",
        ),
        (
            _repo(_bs(StatusKind.CONFLICT_RISK, conflict_paths=["a"]), unpushed=2),
            "conflict risk - 1 file(s) overlap (main)",
        ),
    ],
)
def test_repo_summary(status, expected):
    assert status_text.repo_summary(status) == expected


def test_repo_summary_describes_the_worst_base_not_the_first():
    status = _repo(
        _bs(StatusKind.UP_TO_DATE, base="main"),
        _bs(StatusKind.DIVERGED, behind=4, ahead=1, base="release"),
    )

    assert status_text.repo_summary(status) == "diverged - 4 behind, 1 ahead (release)"


def test_summaries_use_plain_hyphens_never_em_dashes():
    em_dash = chr(0x2014)
    statuses = [
        _repo(_bs(StatusKind.DIVERGED, behind=1, ahead=1)),
        _repo(_bs(StatusKind.CONFLICT_RISK, conflict_paths=["a"])),
        _repo(_bs(StatusKind.CHECK_FAILED, reason="x")),
        _repo(failure_reason="x"),
    ]

    assert all(em_dash not in status_text.repo_summary(status) for status in statuses)

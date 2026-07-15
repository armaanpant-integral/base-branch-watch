"""Tests for core.pr_status.check_pr — gh subprocess invocation + parsing.

subprocess.run is mocked — no real `gh` invocation in tests (mirrors
tests/notify/test_osascript_notifier.py's mocked-subprocess style).

RED until Task 2 implements core/pr_status.py::check_pr — these tests
reference check_pr, so they fail with ImportError/AttributeError, not wrong
assertions, until then.
"""

from __future__ import annotations

import subprocess
from unittest.mock import patch

from base_branch_watch.core.models import PrStatusKind


def _completed(returncode: int, stdout: str = "", stderr: str = "") -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(
        args=["gh"], returncode=returncode, stdout=stdout, stderr=stderr
    )


def test_check_pr_open_returns_open_kind_with_parsed_fields():
    from base_branch_watch.core import pr_status

    view_result = _completed(
        0,
        stdout=(
            '{"number":42,"state":"OPEN","mergeable":"MERGEABLE",'
            '"mergeStateStatus":"CLEAN","reviewDecision":"APPROVED",'
            '"baseRefName":"main"}'
        ),
    )
    checks_result = _completed(0, stdout='[{"bucket":"pass"},{"bucket":"pass"},{"bucket":"pass"}]')

    with patch(
        "base_branch_watch.core.pr_status.GH", "/usr/local/bin/gh"
    ), patch(
        "base_branch_watch.core.pr_status.subprocess.run",
        side_effect=[view_result, checks_result],
    ):
        status = pr_status.check_pr("/tmp/repo")

    assert status.kind == PrStatusKind.OPEN
    assert status.number == 42
    assert status.review_decision == "APPROVED"
    assert status.merge_state_status == "CLEAN"
    assert status.base_ref == "main"
    assert status.checks_pass == 3
    assert status.checks_total == 3


def test_check_pr_open_aggregates_checks_bucket_counts():
    from base_branch_watch.core import pr_status

    view_result = _completed(
        0,
        stdout=(
            '{"number":7,"state":"OPEN","mergeable":"CONFLICTING",'
            '"mergeStateStatus":"DIRTY","reviewDecision":null,'
            '"baseRefName":"main"}'
        ),
    )
    bucket_values = ["pass"] * 8 + ["fail"] * 3
    checks_stdout = "[" + ",".join(f'{{"bucket":"{b}"}}' for b in bucket_values) + "]"
    checks_result = _completed(0, stdout=checks_stdout)

    with patch(
        "base_branch_watch.core.pr_status.GH", "/usr/local/bin/gh"
    ), patch(
        "base_branch_watch.core.pr_status.subprocess.run",
        side_effect=[view_result, checks_result],
    ):
        status = pr_status.check_pr("/tmp/repo")

    assert status.kind == PrStatusKind.OPEN
    assert status.checks_pass == 8
    assert status.checks_fail == 3
    assert status.checks_total == 11


def test_check_pr_no_pr_returns_no_pr_kind_with_current_branch():
    from base_branch_watch.core import pr_status

    view_result = _completed(1, stderr='no pull requests found for branch "feature"\n')

    with patch(
        "base_branch_watch.core.pr_status.GH", "/usr/local/bin/gh"
    ), patch(
        "base_branch_watch.core.pr_status.subprocess.run", return_value=view_result
    ), patch(
        "base_branch_watch.core.pr_status.git_ops.current_branch", return_value="feature"
    ):
        status = pr_status.check_pr("/tmp/repo")

    assert status.kind == PrStatusKind.NO_PR
    assert status.current_branch == "feature"


def test_check_pr_not_installed_when_gh_missing():
    from base_branch_watch.core import pr_status

    with patch("base_branch_watch.core.pr_status.GH", None):
        status = pr_status.check_pr("/tmp/repo")

    assert status.kind == PrStatusKind.NOT_INSTALLED


def test_check_pr_other_failure_returns_check_failed():
    from base_branch_watch.core import pr_status

    view_result = _completed(1, stderr="some other gh error")

    with patch(
        "base_branch_watch.core.pr_status.GH", "/usr/local/bin/gh"
    ), patch(
        "base_branch_watch.core.pr_status.subprocess.run", return_value=view_result
    ):
        status = pr_status.check_pr("/tmp/repo")

    assert status.kind == PrStatusKind.CHECK_FAILED
    assert status.reason == "some other gh error"


def test_check_pr_timeout_returns_check_failed():
    from base_branch_watch.core import pr_status

    with patch(
        "base_branch_watch.core.pr_status.GH", "/usr/local/bin/gh"
    ), patch(
        "base_branch_watch.core.pr_status.subprocess.run",
        side_effect=subprocess.TimeoutExpired(cmd="gh", timeout=15),
    ):
        status = pr_status.check_pr("/tmp/repo")

    assert status.kind == PrStatusKind.CHECK_FAILED


def test_pr_status_module_is_pure_no_rumps_or_appkit_import():
    """ARCH-01 — mirrors tests/runner/test_batch.py's equivalent guard. Checks
    actual import statements, not docstring mentions (the module docstring
    legitimately documents "no rumps/AppKit import, ever", mirroring
    core/git_ops.py's own docstring convention)."""
    import base_branch_watch.core.pr_status as pr_status_module

    src = open(pr_status_module.__file__).read()
    assert "import rumps" not in src
    assert "from rumps" not in src
    assert "import AppKit" not in src
    assert "from AppKit" not in src

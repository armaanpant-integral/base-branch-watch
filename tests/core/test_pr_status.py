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


# -- Task 1 (Plan 02): NOT_AUTHENTICATED / RATE_LIMITED + rate-limit reset --


def test_check_pr_returncode_4_returns_not_authenticated():
    from base_branch_watch.core import pr_status

    view_result = _completed(
        4, stderr="To get started with GitHub CLI, please run:  gh auth login\n"
    )

    with patch(
        "base_branch_watch.core.pr_status.GH", "/usr/local/bin/gh"
    ), patch(
        "base_branch_watch.core.pr_status.subprocess.run", return_value=view_result
    ):
        status = pr_status.check_pr("/tmp/repo")

    assert status.kind == PrStatusKind.NOT_AUTHENTICATED


def test_check_pr_rate_limit_stderr_returns_rate_limited_with_retry_at():
    from base_branch_watch.core import pr_status

    view_result = _completed(1, stderr="API rate limit exceeded for installation ID 12345.\n")

    with patch(
        "base_branch_watch.core.pr_status.GH", "/usr/local/bin/gh"
    ), patch(
        "base_branch_watch.core.pr_status.subprocess.run", return_value=view_result
    ), patch(
        "base_branch_watch.core.pr_status.rate_limit_reset_text", return_value="14:30"
    ):
        status = pr_status.check_pr("/tmp/repo")

    assert status.kind == PrStatusKind.RATE_LIMITED
    assert status.retry_at == "14:30"


def test_check_pr_rate_limit_lowercase_message_also_detected():
    from base_branch_watch.core import pr_status

    view_result = _completed(1, stderr="you have exceeded a secondary rate limit\n")

    with patch(
        "base_branch_watch.core.pr_status.GH", "/usr/local/bin/gh"
    ), patch(
        "base_branch_watch.core.pr_status.subprocess.run", return_value=view_result
    ), patch(
        "base_branch_watch.core.pr_status.rate_limit_reset_text", return_value=None
    ):
        status = pr_status.check_pr("/tmp/repo")

    assert status.kind == PrStatusKind.RATE_LIMITED
    assert status.retry_at is None


def test_rate_limit_reset_text_formats_epoch_to_hh_mm():
    from base_branch_watch.core import pr_status

    # A known epoch — assert against the same local-time formatting the
    # implementation uses, rather than hardcoding a timezone-dependent string.
    import datetime

    reset_epoch = 1784097752
    expected = datetime.datetime.fromtimestamp(reset_epoch).strftime("%H:%M")

    rate_limit_result = _completed(
        0,
        stdout=(
            '{"resources":{"graphql":{"limit":5000,"remaining":4989,'
            f'"reset":{reset_epoch},"used":11}}}}'
        ),
    )

    with patch(
        "base_branch_watch.core.pr_status.GH", "/usr/local/bin/gh"
    ), patch(
        "base_branch_watch.core.pr_status.subprocess.run", return_value=rate_limit_result
    ):
        result = pr_status.rate_limit_reset_text()

    assert result == expected


def test_rate_limit_reset_text_returns_none_on_failure():
    from base_branch_watch.core import pr_status

    with patch(
        "base_branch_watch.core.pr_status.GH", "/usr/local/bin/gh"
    ), patch(
        "base_branch_watch.core.pr_status.subprocess.run",
        side_effect=subprocess.TimeoutExpired(cmd="gh", timeout=10),
    ):
        result = pr_status.rate_limit_reset_text()

    assert result is None


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

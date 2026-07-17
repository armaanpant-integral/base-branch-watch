"""Tests for core.pr_status.check_pr — gh subprocess invocation + parsing.

subprocess.run is mocked — no real `gh` invocation in tests (mirrors
tests/notify/test_osascript_notifier.py's mocked-subprocess style).

RED until Task 2 implements core/pr_status.py::check_pr — these tests
reference check_pr, so they fail with ImportError/AttributeError, not wrong
assertions, until then.
"""

from __future__ import annotations

import datetime
import json
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
    reset_epoch = 1784097752
    expected = datetime.datetime.fromtimestamp(reset_epoch).strftime("%H:%M")

    resources = {"graphql": {"limit": 5000, "remaining": 4989, "reset": reset_epoch, "used": 11}}
    rate_limit_result = _completed(0, stdout=json.dumps({"resources": resources}))

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


# -- CR-01 regression (code review, Phase 04): `gh pr view`'s primary call
# keeps resolving to a MERGED/CLOSED PR directly (branch not deleted) --
# check_pr must detect this itself, not rely solely on final_state(). --------


def test_check_pr_open_call_returns_merged_kind_directly():
    from base_branch_watch.core import pr_status

    view_result = _completed(0, stdout='{"number":42,"state":"MERGED"}')

    with patch(
        "base_branch_watch.core.pr_status.GH", "/usr/local/bin/gh"
    ), patch(
        "base_branch_watch.core.pr_status.subprocess.run", return_value=view_result
    ):
        status = pr_status.check_pr("/tmp/repo")

    assert status.kind == PrStatusKind.MERGED
    assert status.number == 42


def test_check_pr_open_call_returns_closed_kind_directly():
    from base_branch_watch.core import pr_status

    view_result = _completed(0, stdout='{"number":7,"state":"CLOSED"}')

    with patch(
        "base_branch_watch.core.pr_status.GH", "/usr/local/bin/gh"
    ), patch(
        "base_branch_watch.core.pr_status.subprocess.run", return_value=view_result
    ):
        status = pr_status.check_pr("/tmp/repo")

    assert status.kind == PrStatusKind.CLOSED
    assert status.number == 7


# -- WR-02 regression: _checks_counts guards non-dict bucket elements --------


def test_check_pr_open_with_non_dict_bucket_elements_does_not_raise():
    from base_branch_watch.core import pr_status

    view_result = _completed(0, stdout='{"number":1,"state":"OPEN"}')
    # Malformed/unexpected `gh pr checks` output: a list of strings, not dicts.
    checks_result = _completed(0, stdout='["pass", "fail"]')

    with patch(
        "base_branch_watch.core.pr_status.GH", "/usr/local/bin/gh"
    ), patch(
        "base_branch_watch.core.pr_status.subprocess.run",
        side_effect=[view_result, checks_result],
    ):
        status = pr_status.check_pr("/tmp/repo")

    assert status.kind == PrStatusKind.OPEN
    assert status.checks_pass == 0
    assert status.checks_fail == 0


# -- WR-04 regression: checks-fetch failure is distinguishable from a
# genuinely empty checks list. --------------------------------------------


def test_check_pr_open_with_checks_fetch_timeout_sets_unavailable_sentinel():
    from base_branch_watch.core import pr_status

    view_result = _completed(0, stdout='{"number":1,"state":"OPEN"}')

    def run_side_effect(*args, **kwargs):
        cmd = args[0] if args else kwargs.get("args")
        if "checks" in cmd:
            raise subprocess.TimeoutExpired(cmd="gh", timeout=15)
        return view_result

    with patch(
        "base_branch_watch.core.pr_status.GH", "/usr/local/bin/gh"
    ), patch(
        "base_branch_watch.core.pr_status.subprocess.run", side_effect=run_side_effect
    ):
        status = pr_status.check_pr("/tmp/repo")

    assert status.kind == PrStatusKind.OPEN
    assert status.checks_total == -1


def test_check_pr_open_with_zero_checks_configured_reports_zero_not_unavailable():
    from base_branch_watch.core import pr_status

    view_result = _completed(0, stdout='{"number":1,"state":"OPEN"}')
    checks_result = _completed(
        1, stdout="", stderr="no checks reported on the 'main' branch\n"
    )

    with patch(
        "base_branch_watch.core.pr_status.GH", "/usr/local/bin/gh"
    ), patch(
        "base_branch_watch.core.pr_status.subprocess.run",
        side_effect=[view_result, checks_result],
    ):
        status = pr_status.check_pr("/tmp/repo")

    assert status.kind == PrStatusKind.OPEN
    assert status.checks_total == 0
    assert status.checks_pass == 0
    assert status.checks_fail == 0
    assert status.checks_pending == 0


# -- Task 3 (Plan 02): final_state() — D-03 merged/closed one-cycle probe --


def test_final_state_maps_merged():
    from base_branch_watch.core import pr_status

    view_result = _completed(0, stdout='{"state":"MERGED"}')
    with patch(
        "base_branch_watch.core.pr_status.GH", "/usr/local/bin/gh"
    ), patch(
        "base_branch_watch.core.pr_status.subprocess.run", return_value=view_result
    ):
        kind = pr_status.final_state("/tmp/repo", 42)

    assert kind == PrStatusKind.MERGED


def test_final_state_maps_closed():
    from base_branch_watch.core import pr_status

    view_result = _completed(0, stdout='{"state":"CLOSED"}')
    with patch(
        "base_branch_watch.core.pr_status.GH", "/usr/local/bin/gh"
    ), patch(
        "base_branch_watch.core.pr_status.subprocess.run", return_value=view_result
    ):
        kind = pr_status.final_state("/tmp/repo", 42)

    assert kind == PrStatusKind.CLOSED


def test_final_state_maps_other_state_to_no_pr():
    from base_branch_watch.core import pr_status

    view_result = _completed(0, stdout='{"state":"OPEN"}')
    with patch(
        "base_branch_watch.core.pr_status.GH", "/usr/local/bin/gh"
    ), patch(
        "base_branch_watch.core.pr_status.subprocess.run", return_value=view_result
    ):
        kind = pr_status.final_state("/tmp/repo", 42)

    assert kind == PrStatusKind.NO_PR


def test_final_state_never_raises_on_failure_maps_to_no_pr():
    from base_branch_watch.core import pr_status

    with patch(
        "base_branch_watch.core.pr_status.GH", "/usr/local/bin/gh"
    ), patch(
        "base_branch_watch.core.pr_status.subprocess.run",
        side_effect=subprocess.TimeoutExpired(cmd="gh", timeout=15),
    ):
        kind = pr_status.final_state("/tmp/repo", 42)

    assert kind == PrStatusKind.NO_PR


def test_final_state_nonzero_exit_maps_to_no_pr():
    from base_branch_watch.core import pr_status

    view_result = _completed(1, stderr="no pull request found")
    with patch(
        "base_branch_watch.core.pr_status.GH", "/usr/local/bin/gh"
    ), patch(
        "base_branch_watch.core.pr_status.subprocess.run", return_value=view_result
    ):
        kind = pr_status.final_state("/tmp/repo", 42)

    assert kind == PrStatusKind.NO_PR


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

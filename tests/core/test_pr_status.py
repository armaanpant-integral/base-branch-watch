"""Tests for core.pr_status.PrStatusReader - gh output parsing + classification.

The reader takes a `GhClient`; tests use the in-memory FakeGhClient (no
subprocess, no patching). The real subprocess adapter is covered in
test_gh_client.py.
"""

from __future__ import annotations

import datetime
import json

from base_branch_watch.core import pr_status
from base_branch_watch.core.models import PrStatusKind
from tests.core.fake_gh import FakeGhClient, broken, fail, ok

VIEW = ("pr", "view", "--json")
CHECKS = ("pr", "checks")
RATE_LIMIT = ("api", "rate_limit")


def _reader(responses, installed=True, branch="feature") -> pr_status.PrStatusReader:
    return pr_status.PrStatusReader(
        FakeGhClient(responses, installed=installed), current_branch=lambda path: branch
    )


def _checks(*buckets: str) -> str:
    return json.dumps([{"bucket": b} for b in buckets])


# -- check_pr: OPEN ------------------------------------------------------------


def test_check_pr_open_returns_open_kind_with_parsed_fields():
    view = ok(
        '{"number":42,"state":"OPEN","mergeable":"MERGEABLE",'
        '"mergeStateStatus":"CLEAN","reviewDecision":"APPROVED","baseRefName":"main"}'
    )
    reader = _reader({VIEW: view, CHECKS: ok(_checks("pass", "pass", "pass"))})

    status = reader.check_pr("/tmp/repo")

    assert status.kind == PrStatusKind.OPEN
    assert status.number == 42
    assert status.review_decision == "APPROVED"
    assert status.merge_state_status == "CLEAN"
    assert status.base_ref == "main"
    assert status.checks_pass == 3
    assert status.checks_total == 3


def test_check_pr_open_aggregates_checks_bucket_counts():
    view = ok(
        '{"number":7,"state":"OPEN","mergeable":"CONFLICTING",'
        '"mergeStateStatus":"DIRTY","reviewDecision":null,"baseRefName":"main"}'
    )
    reader = _reader({VIEW: view, CHECKS: ok(_checks(*["pass"] * 8, *["fail"] * 3))})

    status = reader.check_pr("/tmp/repo")

    assert status.kind == PrStatusKind.OPEN
    assert status.checks_pass == 8
    assert status.checks_fail == 3
    assert status.checks_total == 11


def test_check_pr_runs_gh_in_the_repo_directory():
    client = FakeGhClient({VIEW: ok('{"number":1,"state":"OPEN"}'), CHECKS: ok("[]")})

    pr_status.PrStatusReader(client, current_branch=lambda path: None).check_pr("/tmp/repo")

    assert {repo_path for repo_path, _args in client.calls} == {"/tmp/repo"}


# -- check_pr: absence / failure sentinels ------------------------------------


def test_check_pr_no_pr_returns_no_pr_kind_with_current_branch():
    reader = _reader({VIEW: fail(1, 'no pull requests found for branch "feature"\n')})

    status = reader.check_pr("/tmp/repo")

    assert status.kind == PrStatusKind.NO_PR
    assert status.current_branch == "feature"


def test_check_pr_not_installed_when_gh_missing():
    status = _reader({}, installed=False).check_pr("/tmp/repo")

    assert status.kind == PrStatusKind.NOT_INSTALLED


def test_check_pr_other_failure_returns_check_failed():
    status = _reader({VIEW: fail(1, "some other gh error")}).check_pr("/tmp/repo")

    assert status.kind == PrStatusKind.CHECK_FAILED
    assert status.reason == "some other gh error"


def test_check_pr_timeout_returns_check_failed_with_the_client_error_text():
    status = _reader({VIEW: broken("gh pr view timed out after 15s")}).check_pr("/tmp/repo")

    assert status.kind == PrStatusKind.CHECK_FAILED
    assert status.reason == "gh pr view timed out after 15s"


def test_check_pr_unparseable_json_returns_check_failed():
    status = _reader({VIEW: ok("not json")}).check_pr("/tmp/repo")

    assert status.kind == PrStatusKind.CHECK_FAILED
    assert status.reason == "gh pr view returned unparseable JSON"


def test_check_pr_returncode_4_returns_not_authenticated():
    reader = _reader(
        {VIEW: fail(4, "To get started with GitHub CLI, please run:  gh auth login\n")}
    )

    assert reader.check_pr("/tmp/repo").kind == PrStatusKind.NOT_AUTHENTICATED


def test_check_pr_rate_limit_stderr_returns_rate_limited_with_retry_at():
    reset_epoch = 1784097752
    resources = {"graphql": {"reset": reset_epoch}}
    reader = _reader(
        {
            VIEW: fail(1, "API rate limit exceeded for installation ID 12345.\n"),
            RATE_LIMIT: ok(json.dumps({"resources": resources})),
        }
    )

    status = reader.check_pr("/tmp/repo")

    assert status.kind == PrStatusKind.RATE_LIMITED
    assert status.retry_at == datetime.datetime.fromtimestamp(reset_epoch).strftime("%H:%M")


def test_check_pr_rate_limit_lowercase_message_also_detected():
    reader = _reader(
        {
            VIEW: fail(1, "you have exceeded a secondary rate limit\n"),
            RATE_LIMIT: broken("gh api rate_limit timed out after 10s"),
        }
    )

    status = reader.check_pr("/tmp/repo")

    assert status.kind == PrStatusKind.RATE_LIMITED
    assert status.retry_at is None


# -- rate_limit_reset_text ----------------------------------------------------


def test_rate_limit_reset_text_formats_epoch_to_hh_mm():
    # Same local-time formatting as the implementation, so no timezone-dependent literal.
    reset_epoch = 1784097752
    expected = datetime.datetime.fromtimestamp(reset_epoch).strftime("%H:%M")
    resources = {"graphql": {"limit": 5000, "remaining": 4989, "reset": reset_epoch, "used": 11}}
    reader = _reader({RATE_LIMIT: ok(json.dumps({"resources": resources}))})

    assert reader.rate_limit_reset_text() == expected


def test_rate_limit_reset_text_returns_none_on_failure():
    reader = _reader({RATE_LIMIT: broken("gh api rate_limit timed out after 10s")})

    assert reader.rate_limit_reset_text() is None


def test_rate_limit_reset_text_returns_none_when_gh_not_installed():
    assert _reader({}, installed=False).rate_limit_reset_text() is None


def test_rate_limit_reset_text_returns_none_on_unexpected_payload():
    assert _reader({RATE_LIMIT: ok('{"resources": {}}')}).rate_limit_reset_text() is None


# -- CR-01: `gh pr view` keeps resolving to a MERGED/CLOSED PR directly -------


def test_check_pr_open_call_returns_merged_kind_directly():
    status = _reader({VIEW: ok('{"number":42,"state":"MERGED"}')}).check_pr("/tmp/repo")

    assert (status.kind, status.number) == (PrStatusKind.MERGED, 42)


def test_check_pr_open_call_returns_closed_kind_directly():
    status = _reader({VIEW: ok('{"number":7,"state":"CLOSED"}')}).check_pr("/tmp/repo")

    assert (status.kind, status.number) == (PrStatusKind.CLOSED, 7)


# -- WR-02: non-dict bucket elements ------------------------------------------


def test_check_pr_open_with_non_dict_bucket_elements_does_not_raise():
    reader = _reader({VIEW: ok('{"number":1,"state":"OPEN"}'), CHECKS: ok('["pass", "fail"]')})

    status = reader.check_pr("/tmp/repo")

    assert status.kind == PrStatusKind.OPEN
    assert status.checks_pass == 0
    assert status.checks_fail == 0


# -- WR-04: checks-fetch failure is distinguishable from zero checks ----------


def test_check_pr_open_with_checks_fetch_timeout_sets_unavailable_sentinel():
    reader = _reader(
        {
            VIEW: ok('{"number":1,"state":"OPEN"}'),
            CHECKS: broken("gh pr checks timed out after 15s"),
        }
    )

    status = reader.check_pr("/tmp/repo")

    assert status.kind == PrStatusKind.OPEN
    assert status.checks_total == -1


def test_check_pr_open_with_zero_checks_configured_reports_zero_not_unavailable():
    reader = _reader(
        {
            VIEW: ok('{"number":1,"state":"OPEN"}'),
            CHECKS: fail(1, "no checks reported on the 'main' branch\n"),
        }
    )

    status = reader.check_pr("/tmp/repo")

    assert status.kind == PrStatusKind.OPEN
    assert (status.checks_total, status.checks_pass, status.checks_fail) == (0, 0, 0)
    assert status.checks_pending == 0


# -- final_state: D-03 merged/closed one-cycle probe --------------------------


def test_final_state_maps_merged():
    reader = _reader({("pr", "view", "42"): ok('{"state":"MERGED"}')})

    assert reader.final_state("/tmp/repo", 42) == PrStatusKind.MERGED


def test_final_state_maps_closed():
    reader = _reader({("pr", "view", "42"): ok('{"state":"CLOSED"}')})

    assert reader.final_state("/tmp/repo", 42) == PrStatusKind.CLOSED


def test_final_state_maps_other_state_to_no_pr():
    reader = _reader({("pr", "view", "42"): ok('{"state":"OPEN"}')})

    assert reader.final_state("/tmp/repo", 42) == PrStatusKind.NO_PR


def test_final_state_never_raises_on_failure_maps_to_no_pr():
    reader = _reader({("pr", "view", "42"): broken("gh pr view timed out after 15s")})

    assert reader.final_state("/tmp/repo", 42) == PrStatusKind.NO_PR


def test_final_state_nonzero_exit_maps_to_no_pr():
    reader = _reader({("pr", "view", "42"): fail(1, "no pull request found")})

    assert reader.final_state("/tmp/repo", 42) == PrStatusKind.NO_PR


def test_final_state_not_installed_maps_to_no_pr():
    assert _reader({}, installed=False).final_state("/tmp/repo", 42) == PrStatusKind.NO_PR


def test_final_state_unparseable_json_maps_to_no_pr():
    assert _reader({("pr", "view", "42"): ok("nope")}).final_state("/tmp/repo", 42) == (
        PrStatusKind.NO_PR
    )


# -- module surface ------------------------------------------------------------


def test_module_level_wrappers_delegate_to_a_default_reader():
    assert callable(pr_status.check_pr)
    assert callable(pr_status.final_state)


def test_pr_status_modules_are_pure_no_rumps_or_appkit_import():
    """ARCH-01: checks actual import statements, not docstring mentions."""
    import base_branch_watch.core.gh_client as gh_client_module

    for module in (pr_status, gh_client_module):
        src = open(module.__file__).read()
        assert "import rumps" not in src
        assert "from rumps" not in src
        assert "import AppKit" not in src
        assert "from AppKit" not in src

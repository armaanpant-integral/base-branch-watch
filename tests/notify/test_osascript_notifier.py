"""Tests for notify.osascript_notifier.OsascriptNotifier.send_digest.

subprocess.run is mocked — no real osascript invocation in tests.
"""

from __future__ import annotations

from unittest.mock import patch

from base_branch_watch.core.models import BranchStatus, RepoStatus, StatusKind
from base_branch_watch.notify.osascript_notifier import OsascriptNotifier


def _behind_status(name: str, behind: int = 3, base: str = "main") -> RepoStatus:
    return RepoStatus(
        repo_path=f"/repos/{name}",
        name=name,
        current_branch="feature",
        unpushed=0,
        branch_statuses=[
            BranchStatus(base=base, behind=behind, ahead_of_base=0, kind=StatusKind.BEHIND)
        ],
    )


def _diverged_status(name: str) -> RepoStatus:
    return RepoStatus(
        repo_path=f"/repos/{name}",
        name=name,
        current_branch="feature",
        unpushed=0,
        branch_statuses=[
            BranchStatus(base="main", behind=3, ahead_of_base=2, kind=StatusKind.DIVERGED)
        ],
    )


def _unpushed_status(name: str, unpushed: int = 2) -> RepoStatus:
    return RepoStatus(
        repo_path=f"/repos/{name}",
        name=name,
        current_branch="feature",
        unpushed=unpushed,
        branch_statuses=[
            BranchStatus(base="main", behind=0, ahead_of_base=0, kind=StatusKind.UP_TO_DATE)
        ],
    )


def test_send_digest_single_repo_invokes_osascript_exactly_once():
    notifier = OsascriptNotifier()
    with patch("base_branch_watch.notify.osascript_notifier.subprocess.run") as mock_run:
        notifier.send_digest([_behind_status("clone")])

    assert mock_run.call_count == 1
    args = mock_run.call_args.args[0]
    assert args[0] == "osascript"
    assert args[1] == "-e"
    script = args[2]
    assert '"1 repo needs attention"' in script
    assert '"Base Branch Watch"' in script
    assert 'sound name "Glass"' in script


def test_send_digest_pluralizes_title_and_joins_body_by_newline():
    notifier = OsascriptNotifier()
    statuses = [_behind_status("a"), _diverged_status("b"), _unpushed_status("c")]
    with patch("base_branch_watch.notify.osascript_notifier.subprocess.run") as mock_run:
        notifier.send_digest(statuses)

    assert mock_run.call_count == 1
    script = mock_run.call_args.args[0][2]
    assert '"3 repos need attention"' in script
    assert "a: 3 behind (main)" in script
    assert "b: diverged — 3 behind, 2 ahead (main)" in script
    assert "c: 2 unpushed" in script


def test_send_digest_truncates_long_body_with_remaining_count_suffix():
    notifier = OsascriptNotifier()
    # 40 repos, each row ~20+ chars — comfortably exceeds the 300-char budget.
    statuses = [_behind_status(f"repo-with-a-long-name-{i:02d}") for i in range(40)]
    with patch("base_branch_watch.notify.osascript_notifier.subprocess.run") as mock_run:
        notifier.send_digest(statuses)

    script = mock_run.call_args.args[0][2]
    assert "…and " in script
    assert " more" in script


def test_send_digest_escapes_double_quotes_in_repo_names():
    notifier = OsascriptNotifier()
    status = _behind_status('weird"repo')
    with patch("base_branch_watch.notify.osascript_notifier.subprocess.run") as mock_run:
        notifier.send_digest([status])

    script = mock_run.call_args.args[0][2]
    assert 'weird\\"repo' in script
    # The AppleScript literal boundaries must not be broken by the raw quote.
    assert script.count('display notification "') == 1


def test_send_digest_empty_list_makes_zero_osascript_calls():
    notifier = OsascriptNotifier()
    with patch("base_branch_watch.notify.osascript_notifier.subprocess.run") as mock_run:
        notifier.send_digest([])

    mock_run.assert_not_called()

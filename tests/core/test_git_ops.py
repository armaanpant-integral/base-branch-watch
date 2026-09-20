from __future__ import annotations

import subprocess
from unittest.mock import patch

import pytest

from base_branch_watch.core import git_ops, log
from base_branch_watch.core.models import RepoConfig, Severity, StatusKind


def test_check_repo_behind_reports_behind_count_and_kind(fixture_repos, default_branch_name):
    _origin, clone_path = fixture_repos

    status = git_ops.check_repo(
        RepoConfig(repo_path=clone_path, base_branches=[default_branch_name])
    )

    assert status.failure_reason is None
    assert len(status.branch_statuses) == 1
    branch_status = status.branch_statuses[0]
    assert branch_status.behind > 0
    assert branch_status.ahead_of_base == 0
    assert branch_status.kind == StatusKind.BEHIND
    assert status.worst_kind == StatusKind.BEHIND
    assert status.severity == Severity.ATTENTION


def test_check_repo_up_to_date(fixture_repos_up_to_date, default_branch_name):
    _origin, clone_path = fixture_repos_up_to_date

    status = git_ops.check_repo(
        RepoConfig(repo_path=clone_path, base_branches=[default_branch_name])
    )

    assert status.failure_reason is None
    assert status.worst_kind == StatusKind.UP_TO_DATE
    assert status.branch_statuses[0].behind == 0


def test_check_repo_nonexistent_path_never_raises(tmp_path):
    missing = tmp_path / "does-not-exist"

    status = git_ops.check_repo(RepoConfig(repo_path=str(missing), base_branches=["main"]))

    assert status.failure_reason is not None
    assert status.worst_kind == StatusKind.CHECK_FAILED


def test_detect_default_branch_returns_fixture_default(fixture_repos, default_branch_name):
    origin_path, clone_path = fixture_repos

    detected = git_ops.detect_default_branch(clone_path)

    assert detected == default_branch_name


def test_check_repo_diverged_reports_behind_and_ahead(fixture_repos_diverged, default_branch_name):
    _origin, clone_path = fixture_repos_diverged

    status = git_ops.check_repo(
        RepoConfig(repo_path=clone_path, base_branches=[default_branch_name])
    )

    assert status.failure_reason is None
    branch_status = status.branch_statuses[0]
    assert branch_status.behind > 0
    assert branch_status.ahead_of_base > 0
    assert branch_status.kind == StatusKind.DIVERGED
    assert status.worst_kind == StatusKind.DIVERGED
    assert status.severity == Severity.BLOCKING


def test_check_repo_unpushed_only(fixture_repos_unpushed, default_branch_name):
    _origin, clone_path = fixture_repos_unpushed

    status = git_ops.check_repo(
        RepoConfig(repo_path=clone_path, base_branches=[default_branch_name])
    )

    assert status.failure_reason is None
    assert status.unpushed > 0
    assert status.branch_statuses[0].kind == StatusKind.UP_TO_DATE
    assert status.worst_kind == StatusKind.UNPUSHED
    assert status.severity == Severity.ATTENTION


def test_check_repo_behind_and_unpushed(fixture_repos_behind_and_unpushed, default_branch_name):
    _origin, clone_path = fixture_repos_behind_and_unpushed

    status = git_ops.check_repo(
        RepoConfig(repo_path=clone_path, base_branches=[default_branch_name])
    )

    assert status.failure_reason is None
    assert status.unpushed > 0
    branch_status = status.branch_statuses[0]
    assert branch_status.behind > 0
    assert branch_status.ahead_of_base == 0
    assert branch_status.kind == StatusKind.BEHIND
    assert status.worst_kind == StatusKind.BEHIND_AND_UNPUSHED
    assert status.severity == Severity.ATTENTION


def test_check_repo_multi_base_worst_wins(fixture_repos_multi_base, default_branch_name):
    origin_path, clone_path = fixture_repos_multi_base

    status = git_ops.check_repo(
        RepoConfig(repo_path=clone_path, base_branches=[default_branch_name, "release"])
    )

    assert status.failure_reason is None
    assert len(status.branch_statuses) == 2
    by_base = {bs.base: bs for bs in status.branch_statuses}
    assert by_base[default_branch_name].kind == StatusKind.DIVERGED
    assert by_base["release"].kind == StatusKind.UP_TO_DATE
    assert status.worst_kind == StatusKind.DIVERGED
    assert status.severity == Severity.BLOCKING


def test_unpushed_count_zero_when_no_upstream(fixture_repo_no_upstream):
    count = git_ops.unpushed_count(fixture_repo_no_upstream)

    assert count == 0


def test_fetch_rejects_dash_prefixed_base_as_flag(fixture_repos):
    """A base-branch string starting with '-' must never be parsed as a git
    option (argument-injection guard). With the '--' positional separator in
    place, git reports a missing ref rather than acting on the flag."""
    _origin, clone_path = fixture_repos

    result = git_ops.fetch(clone_path, "--upload-pack=/bin/false")

    assert result.ok is False
    # git treated the whole string as a literal (nonexistent) ref name, not
    # as an --upload-pack option -- proof the '--' separator holds.
    assert "couldn't find remote ref" in (result.error or "").lower()


def test_check_repo_fetch_failure_is_distinct_not_bogus_behind(
    fixture_repos_fetch_fails, default_branch_name, bbw_config_dir
):
    _origin, clone_path = fixture_repos_fetch_fails

    status = git_ops.check_repo(
        RepoConfig(repo_path=clone_path, base_branches=[default_branch_name])
    )

    branch_status = status.branch_statuses[0]
    assert branch_status.kind == StatusKind.CHECK_FAILED
    assert branch_status.behind == 0
    # Real git stderr for a remote path that is gone, not a canned "SSH" guess.
    assert branch_status.reason is not None
    assert branch_status.reason.startswith("fetch failed (origin is not a git repository)")
    assert "ssh" not in branch_status.reason.lower()
    assert status.worst_kind == StatusKind.CHECK_FAILED
    assert status.severity == Severity.BLOCKING
    logged_text = log.log_path().read_text()
    assert "does not appear to be a git repository" in logged_text
    assert f"base {default_branch_name}" in logged_text


REAL_GIT_FETCH_ERRORS = [
    (
        "ssh: Could not resolve hostname github.com: nodename nor servname provided, or not known\n"
        "fatal: Could not read from remote repository.\n",
        "network unreachable",
    ),
    (
        "fatal: unable to access 'https://github.com/o/r.git/': "
        "Could not resolve host: github.com\n",
        "network unreachable",
    ),
    (
        "ssh: connect to host github.com port 22: Operation timed out\n"
        "fatal: Could not read from remote repository.\n",
        "network unreachable",
    ),
    (
        "git@github.com: Permission denied (publickey).\n"
        "fatal: Could not read from remote repository.\n",
        "auth or access denied",
    ),
    (
        "remote: Repository not found.\n"
        "fatal: repository 'https://github.com/o/r.git/' not found\n",
        "auth or access denied",
    ),
    (
        "fatal: could not read Username for 'https://github.com': terminal prompts disabled\n",
        "auth or access denied",
    ),
    ("fatal: couldn't find remote ref main\n", "base branch not found on origin"),
    (
        "error: cannot lock ref 'refs/remotes/origin/main': is at abc but expected def\n",
        "ref locked",
    ),
    (
        "fatal: Unable to create '/r/.git/refs/remotes/origin/main.lock': File exists.\n",
        "ref locked",
    ),
    (
        "fatal: '/gone/remote' does not appear to be a git repository\n"
        "fatal: Could not read from remote repository.\n",
        "origin is not a git repository",
    ),
    ("fetch timed out after 15s", "timeout"),
    (
        "fatal: unable to access 'https://github.com/o/r.git/': "
        "Failed to connect to github.com port 443 after 75003 ms: Couldn't connect to server\n",
        "network unreachable",
    ),
    (
        "fatal: unable to access 'https://github.com/o/r.git/': "
        "Couldn't connect to server\n",
        "network unreachable",
    ),
    (
        "fatal: unable to access 'https://github.com/o/r.git/': "
        "LibreSSL SSL_connect: SSL_ERROR_SYSCALL in connection to github.com:443 \n",
        "network unreachable",
    ),
    (
        "fatal: unable to access 'https://git.corp.example/o/r.git/': "
        "SSL certificate problem: unable to get local issuer certificate\n",
        "tls certificate problem",
    ),
    (
        "fatal: unable to access 'https://github.com/o/r.git/': "
        "The requested URL returned error: 401\n",
        "auth or access denied",
    ),
    (
        "fatal: unable to access 'https://github.com/o/r.git/': "
        "The requested URL returned error: 403\n",
        "auth or access denied",
    ),
    (
        "fatal: unable to access 'https://github.com/o/r.git/': "
        "The requested URL returned error: 503\n",
        "remote server error",
    ),
    (
        "fatal: unable to access 'https://github.com/o/r.git/': "
        "The requested URL returned error: 502\n",
        "remote server error",
    ),
    # Generic git trailer alone must not be guessed into a specific cause.
    ("fatal: Could not read from remote repository.\n", "other"),
    ("", "other"),
    (None, "other"),
]


@pytest.mark.parametrize("fetch_error_text, expected_cause_label", REAL_GIT_FETCH_ERRORS)
def test_classify_fetch_error_maps_real_git_stderr(fetch_error_text, expected_cause_label):
    assert git_ops.classify_fetch_error(fetch_error_text) == expected_cause_label


def test_fetch_failure_reason_uses_first_stderr_line_and_never_says_ssh_for_https():
    https_error_text = (
        "fatal: unable to access 'https://github.com/o/r.git/': "
        "Could not resolve host: github.com\n"
        "fatal: second line that must not appear\n"
    )

    reason = git_ops.fetch_failure_reason(https_error_text)

    assert reason == (
        "fetch failed (network unreachable): fatal: unable to access "
        "'https://github.com/o/r.git/': Could"
    )
    assert "ssh" not in reason.lower()
    assert "second line" not in reason


def test_fetch_failure_reason_caps_excerpt_length():
    long_error_text = "fatal: " + "x" * 500

    reason = git_ops.fetch_failure_reason(long_error_text)

    assert git_ops.FETCH_ERROR_EXCERPT_CAP == 60
    assert reason == "fetch failed (other): " + "fatal: " + "x" * 53


def test_fetch_failure_reason_without_error_text_has_label_only():
    assert git_ops.fetch_failure_reason("") == "fetch failed (other)"


def test_fetch_returns_git_stderr_from_mocked_run_git_on_nonzero_exit():
    denied = subprocess.CompletedProcess(
        args=["git"],
        returncode=128,
        stdout="",
        stderr=(
            "git@github.com: Permission denied (publickey).\n"
            "fatal: Could not read from remote repository.\n"
        ),
    )

    with patch("base_branch_watch.core.git_ops._run_git", return_value=denied):
        result = git_ops.fetch("/any/repo", "main")

    assert result.ok is False
    assert git_ops.classify_fetch_error(result.error) == "auth or access denied"


def test_fetch_timeout_from_mocked_run_git_is_classified_as_timeout():
    with patch(
        "base_branch_watch.core.git_ops._run_git",
        side_effect=subprocess.TimeoutExpired(cmd="git", timeout=15),
    ):
        result = git_ops.fetch("/any/repo", "main")

    assert result.ok is False
    assert git_ops.classify_fetch_error(result.error) == "timeout"


def test_check_repo_logs_raw_fetch_error_with_repo_base_and_cause(
    fixture_repos, default_branch_name, bbw_config_dir
):
    _origin, clone_path = fixture_repos
    raw_error_text = (
        "git@github.com: Permission denied (publickey).\n"
        "fatal: Could not read from remote repository.\n"
    )

    with patch(
        "base_branch_watch.core.git_ops.fetch_with_retry",
        return_value=git_ops.FetchResult(ok=False, error=raw_error_text),
    ):
        status = git_ops.check_repo(
            RepoConfig(repo_path=clone_path, base_branches=[default_branch_name])
        )

    branch_status = status.branch_statuses[0]
    assert branch_status.kind == StatusKind.CHECK_FAILED
    assert branch_status.reason == (
        "fetch failed (auth or access denied): git@github.com: Permission denied (publickey)."
    )
    logged_lines = log.log_path().read_text().splitlines()
    assert logged_lines == [
        f"[FAIL] fetch failed for {clone_path} (base {default_branch_name}, "
        "cause auth or access denied): "
        "git@github.com: Permission denied (publickey). | "
        "fatal: Could not read from remote repository."
    ]


def test_check_repo_keeps_reason_short_but_logs_full_stderr_line(
    fixture_repos, default_branch_name, bbw_config_dir
):
    _origin, clone_path = fixture_repos
    long_first_line = (
        "fatal: unable to access 'https://github.com/o/r.git/': "
        "The requested URL returned error: 403"
    )

    with patch(
        "base_branch_watch.core.git_ops.fetch_with_retry",
        return_value=git_ops.FetchResult(ok=False, error=long_first_line + "\n"),
    ):
        status = git_ops.check_repo(
            RepoConfig(repo_path=clone_path, base_branches=[default_branch_name])
        )

    reason = status.branch_statuses[0].reason
    assert reason == (
        "fetch failed (auth or access denied): fatal: unable to access "
        "'https://github.com/o/r.git/': The r"
    )
    assert "ssh" not in reason.lower()
    assert log.log_path().read_text().splitlines() == [
        f"[FAIL] fetch failed for {clone_path} (base {default_branch_name}, "
        f"cause auth or access denied): {long_first_line}"
    ]


def test_check_repo_fetch_failure_survives_log_write_error(fixture_repos, default_branch_name):
    _origin, clone_path = fixture_repos

    with (
        patch(
            "base_branch_watch.core.git_ops.fetch_with_retry",
            return_value=git_ops.FetchResult(ok=False, error="fetch timed out after 15s"),
        ),
        patch("base_branch_watch.core.git_ops.log.append", side_effect=OSError("disk full")),
    ):
        status = git_ops.check_repo(
            RepoConfig(repo_path=clone_path, base_branches=[default_branch_name])
        )

    assert status.branch_statuses[0].kind == StatusKind.CHECK_FAILED
    assert status.branch_statuses[0].reason == "fetch failed (timeout): fetch timed out after 15s"


def test_check_repo_conflict_risk_when_local_and_incoming_overlap(
    fixture_repos_conflict_overlap, default_branch_name
):
    _origin, clone_path = fixture_repos_conflict_overlap

    status = git_ops.check_repo(
        RepoConfig(repo_path=clone_path, base_branches=[default_branch_name])
    )

    assert status.failure_reason is None
    branch_status = status.branch_statuses[0]
    assert branch_status.kind == StatusKind.CONFLICT_RISK
    assert branch_status.conflict_paths == ["shared.txt"]
    assert status.worst_kind == StatusKind.CONFLICT_RISK
    assert status.severity == Severity.BLOCKING


def test_check_repo_behind_without_overlap_stays_behind(
    fixture_repos_behind_no_overlap, default_branch_name
):
    _origin, clone_path = fixture_repos_behind_no_overlap

    status = git_ops.check_repo(
        RepoConfig(repo_path=clone_path, base_branches=[default_branch_name])
    )

    assert status.failure_reason is None
    branch_status = status.branch_statuses[0]
    assert branch_status.kind in (StatusKind.BEHIND, StatusKind.DIVERGED)
    assert branch_status.conflict_paths == []


def test_check_repo_conflict_risk_on_incoming_rename_of_locally_edited_old_path(
    fixture_repos_incoming_rename, default_branch_name
):
    """RESEARCH Pitfall 1 regression: a local branch-unique edit to file.txt
    still overlap-matches an incoming rename of file.txt -> renamed.txt,
    because _parse_name_status_z adds both R-record paths to the incoming
    set. A naive --name-only parse would NOT flag this."""
    _origin, clone_path = fixture_repos_incoming_rename

    status = git_ops.check_repo(
        RepoConfig(repo_path=clone_path, base_branches=[default_branch_name])
    )

    assert status.failure_reason is None
    branch_status = status.branch_statuses[0]
    assert branch_status.kind == StatusKind.CONFLICT_RISK
    assert "file.txt" in branch_status.conflict_paths


def test_check_repo_no_common_ancestor_is_check_failed_not_no_conflict(
    fixture_repos_no_common_ancestor, default_branch_name
):
    """RESEARCH Pitfall 3/5 regression: a base with no common ancestor (e.g.
    rewritten/orphan history) makes merge_base() return None, which must
    route to CHECK_FAILED -- never a silent CONFLICT_RISK-empty or a bogus
    BEHIND with no warning."""
    _origin, clone_path = fixture_repos_no_common_ancestor

    status = git_ops.check_repo(
        RepoConfig(repo_path=clone_path, base_branches=[default_branch_name])
    )

    branch_status = status.branch_statuses[0]
    assert branch_status.kind == StatusKind.CHECK_FAILED
    assert branch_status.conflict_paths == []
    assert branch_status.reason == "conflict check failed — local git error"

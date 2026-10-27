"""SubprocessGhClient contract: a stub `gh` executable on PATH, no patching."""

from __future__ import annotations

import stat

import pytest

from base_branch_watch.core.gh_client import SubprocessGhClient


def _install_stub_gh(tmp_path, monkeypatch, body: str) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    stub = bin_dir / "gh"
    stub.write_text(f"#!/bin/sh\n{body}\n")
    stub.chmod(stub.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("PATH", str(bin_dir))


def test_not_installed_when_gh_is_not_on_path(monkeypatch, tmp_path):
    monkeypatch.setenv("PATH", str(tmp_path))

    assert SubprocessGhClient().installed is False


def test_installed_is_resolved_per_call_not_at_import(tmp_path, monkeypatch):
    client = SubprocessGhClient()
    monkeypatch.setenv("PATH", str(tmp_path))
    assert client.installed is False

    _install_stub_gh(tmp_path, monkeypatch, "exit 0")

    assert client.installed is True


def test_run_returns_exit_status_stdout_and_stderr(tmp_path, monkeypatch):
    _install_stub_gh(tmp_path, monkeypatch, 'echo "out:$*"; echo "err" >&2; exit 3')

    outcome = SubprocessGhClient().run(str(tmp_path), ["pr", "view"], timeout=5)

    assert (outcome.returncode, outcome.stdout.strip(), outcome.stderr.strip()) == (
        3,
        "out:pr view",
        "err",
    )
    assert outcome.error is None


def test_run_executes_in_the_given_repo_directory(tmp_path, monkeypatch):
    _install_stub_gh(tmp_path, monkeypatch, "pwd")
    repo = tmp_path / "repo"
    repo.mkdir()

    outcome = SubprocessGhClient().run(str(repo), ["pr", "view"], timeout=5)

    assert outcome.stdout.strip().endswith("/repo")


def test_run_timeout_is_an_error_outcome_not_an_exception(tmp_path, monkeypatch):
    _install_stub_gh(tmp_path, monkeypatch, "/bin/sleep 5")

    outcome = SubprocessGhClient().run(str(tmp_path), ["pr", "view", "--json", "x"], timeout=1)

    assert outcome.error == "gh pr view timed out after 1s"


def test_run_oserror_is_an_error_outcome_with_the_os_message(tmp_path, monkeypatch):
    _install_stub_gh(tmp_path, monkeypatch, "exit 0")

    outcome = SubprocessGhClient().run(str(tmp_path / "missing-dir"), ["pr", "view"], timeout=5)

    assert outcome.error is not None
    assert "missing-dir" in outcome.error


def test_run_without_gh_installed_is_an_error_outcome(tmp_path, monkeypatch):
    monkeypatch.setenv("PATH", str(tmp_path))

    outcome = SubprocessGhClient().run(None, ["api", "rate_limit"], timeout=5)

    assert outcome.error is not None


@pytest.mark.parametrize("repo_path", [None])
def test_run_without_a_repo_path_runs_in_the_current_directory(repo_path, tmp_path, monkeypatch):
    _install_stub_gh(tmp_path, monkeypatch, "echo ok")

    outcome = SubprocessGhClient().run(repo_path, ["api", "rate_limit"], timeout=5)

    assert outcome.stdout.strip() == "ok"

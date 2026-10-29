"""Stale-branch fan-out: one bounded pass over the watched repos, failures isolated per repo."""

from __future__ import annotations

from base_branch_watch.core.models import RepoConfig, StaleBranch, StaleKind
from base_branch_watch.runner import batch


def _repo(path: str) -> RepoConfig:
    return RepoConfig(repo_path=path, base_branches=["main"])


def test_a_repo_whose_check_raises_is_left_out_without_hiding_the_others():
    found = [StaleBranch(name="feat/x", kind=StaleKind.GONE_MERGED)]

    def check(repo: RepoConfig) -> list[StaleBranch]:
        if repo.repo_path == "/bad":
            raise RuntimeError("boom")
        return found

    result = batch.check_stale_all([_repo("/good"), _repo("/bad")], check=check)

    assert result == {"/good": found}


def test_no_watched_repos_gives_an_empty_result_without_building_a_pool():
    assert batch.check_stale_all([], check=lambda repo: []) == {}


def test_a_repo_whose_stale_state_is_unknown_is_left_out_not_reported_clean():
    found = [StaleBranch(name="feat/x", kind=StaleKind.GONE_MERGED)]

    def check(repo: RepoConfig) -> list[StaleBranch] | None:
        return None if repo.repo_path == "/offline" else found

    result = batch.check_stale_all([_repo("/good"), _repo("/offline")], check=check)

    assert result == {"/good": found}


def test_a_repo_whose_check_raises_is_logged_with_its_path_and_the_real_cause(monkeypatch):
    logged: list[str] = []
    monkeypatch.setattr(batch.log, "append", logged.append)

    def check(repo: RepoConfig) -> list[StaleBranch]:
        raise TimeoutError("git for-each-ref timed out after 10s")

    batch.check_stale_all([_repo("/slow")], check=check)

    assert len(logged) == 1
    assert "/slow" in logged[0] and "git for-each-ref timed out after 10s" in logged[0]
    assert logged[0].startswith("[FAIL]")

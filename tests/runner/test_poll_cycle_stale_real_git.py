"""PollCycle with its DEFAULT stale check: real batch.check_stale_all, real reader, real git.

Only the git-status/PR-status half (check_all) is faked, because it would otherwise call
`gh`. Everything on the stale path is the production default.
"""

from __future__ import annotations

from base_branch_watch.core.models import RepoConfig, RepoStatus, StaleBranch, StaleKind
from base_branch_watch.runner.poll_cycle import PollCycle
from tests.core.git_helpers import commit_on_new_branch, git


def _fake_check_all(repos, pr_repo_paths=None):
    return [RepoStatus.failed(repo, "not under test") for repo in repos], {}


def test_default_wiring_reports_a_real_merged_and_deleted_branch(
    fixture_repos_up_to_date, default_branch_name
):
    origin, clone = fixture_repos_up_to_date
    commit_on_new_branch(clone, "feat/x")
    git(clone, "switch", default_branch_name)
    git(origin, "merge", "--no-ff", "--no-edit", "feat/x")
    git(origin, "branch", "-D", "feat/x")
    git(clone, "fetch", "--quiet", "origin")
    repo = RepoConfig(repo_path=clone, base_branches=[default_branch_name])

    result = PollCycle(check_all=_fake_check_all).run([repo], {}, now=1000.0)

    assert result.stale_statuses == {
        clone: [StaleBranch(name="feat/x", kind=StaleKind.GONE_MERGED)]
    }


def test_default_wiring_leaves_an_unreachable_repo_out_and_says_so(
    fixture_repos_up_to_date, default_branch_name
):
    _origin, clone = fixture_repos_up_to_date
    git(clone, "remote", "set-url", "origin", "/nonexistent/offline.git")
    repo = RepoConfig(repo_path=clone, base_branches=[default_branch_name])

    result = PollCycle(check_all=_fake_check_all).run([repo], {}, now=1000.0)

    assert result.stale_statuses == {}
    assert any("stale branch check skipped" in line for line in result.log_lines)

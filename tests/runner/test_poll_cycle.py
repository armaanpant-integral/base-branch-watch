"""PollCycle: one refresh cycle's decisions, with every collaborator faked.

No git, no gh, no rumps, no patching - the cycle takes its batch checker,
`final_state` probe and `current_branch` lookup as constructor arguments.
"""

from __future__ import annotations

from base_branch_watch.core.models import (
    BranchStatus,
    PrStatus,
    PrStatusKind,
    RepoConfig,
    RepoStatus,
    StaleBranch,
    StaleKind,
    StatusKind,
)
from base_branch_watch.runner import poll_cycle
from base_branch_watch.runner.poll_cycle import PR_CHECK_FLOOR_SECONDS, PollCycle


def _repo(name="repo-a") -> RepoConfig:
    return RepoConfig(repo_path=f"/tmp/{name}", base_branches=["main"])


def _status(name="repo-a", kind=StatusKind.UP_TO_DATE, behind=0, ahead=0, sha="sha-1", **kw):
    return RepoStatus(
        repo_path=f"/tmp/{name}",
        name=name,
        current_branch="feature",
        unpushed=kw.pop("unpushed", 0),
        branch_statuses=[
            BranchStatus(
                base="main", behind=behind, ahead_of_base=ahead, kind=kind, head_sha=sha, **kw
            )
        ],
    )


class FakeBatch:
    """Stands in for runner.batch.check_all; records which repos got a PR check."""

    def __init__(self, statuses: dict[str, RepoStatus], pr: dict[str, PrStatus] | None = None):
        self.statuses = statuses
        self.pr = pr or {}
        self.pr_repo_paths_seen: list[set[str] | None] = []

    def __call__(self, repos, pr_repo_paths=None):
        self.pr_repo_paths_seen.append(pr_repo_paths)
        statuses = [self.statuses[r.repo_path] for r in repos]
        fresh = {
            r.repo_path: self.pr[r.repo_path]
            for r in repos
            if r.repo_path in self.pr and (pr_repo_paths is None or r.repo_path in pr_repo_paths)
        }
        return statuses, fresh


def _cycle(
    batch_fake,
    final_state=lambda path, number: None,
    branch="feature",
    check_stale=lambda repos: {repo.repo_path: [] for repo in repos},
) -> PollCycle:
    return PollCycle(
        check_all=batch_fake,
        final_state=final_state,
        current_branch=lambda path: branch,
        check_stale=check_stale,
    )


def _open(number=7) -> PrStatus:
    return PrStatus(kind=PrStatusKind.OPEN, number=number)


# -- stale branches -----------------------------------------------------------


def test_stale_branches_found_for_a_repo_come_through_on_the_result():
    repo = _repo()
    stale = {repo.repo_path: [StaleBranch(name="feat/x", kind=StaleKind.GONE_MERGED)]}
    cycle = _cycle(FakeBatch({repo.repo_path: _status()}), check_stale=lambda repos: stale)

    result = cycle.run([repo], {}, now=1000.0)

    assert result.stale_statuses == stale


def test_a_repo_skipped_by_the_stale_check_is_logged_once_and_others_are_not():
    checked, skipped = _repo("repo-a"), _repo("repo-b")
    fake = FakeBatch({checked.repo_path: _status("repo-a"), skipped.repo_path: _status("repo-b")})
    cycle = _cycle(fake, check_stale=lambda repos: {checked.repo_path: []})

    result = cycle.run([checked, skipped], {}, now=1000.0)

    stale_lines = [line for line in result.log_lines if "stale" in line]
    assert stale_lines == ["[INFO] repo-b: stale branch check skipped (branches could not be read)"]


def test_a_crashing_stale_check_leaves_git_status_intact_and_is_logged():
    repo = _repo()

    def exploding(repos):
        raise RuntimeError("boom")

    cycle = _cycle(FakeBatch({repo.repo_path: _status()}), check_stale=exploding)

    result = cycle.run([repo], {}, now=1000.0)

    assert [s.name for s in result.statuses] == ["repo-a"]
    assert result.stale_statuses == {}
    assert any("stale" in line and "boom" in line for line in result.log_lines)


# -- PR polling floor ---------------------------------------------------------


def test_first_cycle_is_eligible_even_when_the_monotonic_clock_is_near_zero():
    """WR-03: a machine rebooted <2min ago has monotonic() < the floor; the
    -inf sentinel keeps the first cycle from excluding every repo."""
    repo = _repo()
    fake = FakeBatch({repo.repo_path: _status()}, {repo.repo_path: _open()})
    cycle = _cycle(fake)

    cycle.run([repo], {}, now=5.0)

    assert fake.pr_repo_paths_seen == [{repo.repo_path}]


def test_pr_check_waits_for_the_floor_then_runs_again():
    repo = _repo()
    fake = FakeBatch({repo.repo_path: _status()}, {repo.repo_path: _open()})
    cycle = _cycle(fake)

    cycle.run([repo], {}, now=1000.0)
    cycle.run([repo], {}, now=1000.0 + PR_CHECK_FLOOR_SECONDS - 1)
    cycle.run([repo], {}, now=1000.0 + PR_CHECK_FLOOR_SECONDS)

    assert fake.pr_repo_paths_seen == [{repo.repo_path}, set(), {repo.repo_path}]


def test_repo_skipped_by_the_floor_keeps_its_previous_pr_status():
    repo = _repo()
    fake = FakeBatch({repo.repo_path: _status()}, {repo.repo_path: _open()})
    cycle = _cycle(fake)
    cycle.run([repo], {}, now=1000.0)

    result = cycle.run([repo], {}, now=1001.0)

    assert result.pr_statuses[repo.repo_path].kind == PrStatusKind.OPEN


def test_pr_status_of_a_repo_no_longer_watched_is_dropped():
    a, b = _repo("repo-a"), _repo("repo-b")
    fake = FakeBatch(
        {a.repo_path: _status("repo-a"), b.repo_path: _status("repo-b")},
        {a.repo_path: _open(1), b.repo_path: _open(2)},
    )
    cycle = _cycle(fake)
    cycle.run([a, b], {}, now=0.0)

    result = cycle.run([a], {}, now=1.0)

    assert set(result.pr_statuses) == {a.repo_path}


def test_forget_clears_pr_memory_for_removed_repos():
    repo = _repo()
    fake = FakeBatch({repo.repo_path: _status()}, {repo.repo_path: _open()})
    cycle = _cycle(fake)
    cycle.run([repo], {}, now=0.0)

    cycle.forget([repo.repo_path])

    assert repo.repo_path not in cycle.pr_statuses
    assert repo.repo_path not in cycle.last_pr_checked_at


def test_a_re_added_repo_is_checked_on_the_next_cycle_not_after_the_floor():
    repo = _repo()
    fake = FakeBatch({repo.repo_path: _status()}, {repo.repo_path: _open()})
    cycle = _cycle(fake)
    cycle.run([repo], {}, now=1000.0)

    cycle.forget([repo.repo_path])
    cycle.run([repo], {}, now=1001.0)

    assert fake.pr_repo_paths_seen[-1] == {repo.repo_path}


def test_result_pr_statuses_is_a_snapshot_not_the_cycles_live_memory():
    repo = _repo()
    fake = FakeBatch({repo.repo_path: _status()}, {repo.repo_path: _open()})
    cycle = _cycle(fake)

    result = cycle.run([repo], {}, now=0.0)
    result.pr_statuses.clear()

    assert repo.repo_path in cycle.pr_statuses


# -- MERGED / CLOSED one-cycle collapse ---------------------------------------


def test_route_a_pr_vanishes_and_gh_says_merged_shows_merged_once():
    repo = _repo()
    fake = FakeBatch({repo.repo_path: _status()}, {repo.repo_path: _open(7)})
    cycle = _cycle(fake, final_state=lambda path, number: PrStatusKind.MERGED)
    cycle.run([repo], {}, now=0.0)
    fake.pr = {repo.repo_path: PrStatus(kind=PrStatusKind.NO_PR)}

    shown = cycle.run([repo], {}, now=200.0)
    assert (shown.pr_statuses[repo.repo_path].kind, shown.pr_statuses[repo.repo_path].number) == (
        PrStatusKind.MERGED,
        7,
    )

    after = cycle.run([repo], {}, now=400.0)
    assert after.pr_statuses[repo.repo_path].kind == PrStatusKind.NO_PR


def test_route_a_without_a_terminal_probe_keeps_the_fresh_no_pr():
    repo = _repo()
    fake = FakeBatch({repo.repo_path: _status()}, {repo.repo_path: _open(7)})
    cycle = _cycle(fake, final_state=lambda path, number: None)
    cycle.run([repo], {}, now=0.0)
    fake.pr = {repo.repo_path: PrStatus(kind=PrStatusKind.NO_PR)}

    result = cycle.run([repo], {}, now=200.0)

    assert result.pr_statuses[repo.repo_path].kind == PrStatusKind.NO_PR


def test_route_b_same_terminal_pr_twice_collapses_to_no_pr_on_the_current_branch():
    repo = _repo()
    merged = PrStatus(kind=PrStatusKind.MERGED, number=7)
    fake = FakeBatch({repo.repo_path: _status()}, {repo.repo_path: merged})
    cycle = _cycle(fake, branch="main")

    first = cycle.run([repo], {}, now=0.0)
    second = cycle.run([repo], {}, now=200.0)

    assert first.pr_statuses[repo.repo_path].kind == PrStatusKind.MERGED
    collapsed = second.pr_statuses[repo.repo_path]
    assert collapsed.kind == PrStatusKind.NO_PR
    assert collapsed.current_branch == "main"


# -- notification + logging ---------------------------------------------------


def test_digest_is_returned_once_then_deduped_through_the_returned_state():
    repo = _repo()
    behind = _status(kind=StatusKind.BEHIND, behind=2)
    cycle = _cycle(FakeBatch({repo.repo_path: behind}))

    first = cycle.run([repo], {}, now=0.0)
    second = cycle.run([repo], first.state, now=200.0)

    assert first.digest_statuses == [behind]
    assert first.state != {}
    assert second.digest_statuses == []
    assert second.state == first.state


def test_log_lines_label_check_failed_as_fail_and_the_rest_ok():
    ok, bad, diverged = _repo("ok"), _repo("bad"), _repo("div")
    failed = _status("bad", kind=StatusKind.CHECK_FAILED, reason="fetch failed")
    cycle = _cycle(
        FakeBatch(
            {
                ok.repo_path: _status("ok"),
                bad.repo_path: failed,
                diverged.repo_path: _status("div", kind=StatusKind.DIVERGED, behind=3, ahead=2),
            }
        )
    )

    result = cycle.run([ok, bad, diverged], {}, now=0.0)

    assert result.log_lines == [
        "[OK] ok: up to date",
        "[FAIL] bad: check failed - fetch failed (main)",
        "[OK] div: diverged - 3 behind, 2 ahead (main)",
    ]


def test_status_log_line_for_a_repo_level_failure():
    status = RepoStatus.failed(_repo("gone"), "repo folder not found")

    assert poll_cycle.status_log_line(status) == "[FAIL] gone: repo folder not found"

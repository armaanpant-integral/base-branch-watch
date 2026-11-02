"""assess_base: one per-(repo, base) verdict shared by the poller and the push gate.

Real tmp git repos throughout (see tests/conftest.py fixtures); the verdict
reports facts only - gating and menu status are decided by the callers.
"""

from __future__ import annotations

from pathlib import Path

from base_branch_watch.core import base_verdict, config, git_ops, log
from base_branch_watch.core.models import RepoConfig, StatusKind
from tests.conftest import raise_timeout


def _assess(clone_path, base, local_ref="HEAD", **kwargs):
    return base_verdict.assess_base(clone_path, base, local_ref, **kwargs)


def test_behind_clone_reports_counts_and_post_fetch_head_sha(fixture_repos, default_branch_name):
    _origin, clone_path = fixture_repos

    verdict = _assess(clone_path, default_branch_name)

    assert verdict.fetch_ok is True
    assert verdict.fetch_error is None
    assert verdict.behind > 0
    assert verdict.ahead == 0
    assert verdict.head_sha == git_ops.resolve_ref(clone_path, f"origin/{default_branch_name}")
    assert verdict.merge_conflict is False  # nothing of ours to conflict with
    assert verdict.incoming_commits == []  # opt-in, not paid on the poll path


def test_up_to_date_clone_has_no_incoming_and_no_conflict(
    fixture_repos_up_to_date, default_branch_name
):
    _origin, clone_path = fixture_repos_up_to_date

    verdict = _assess(clone_path, default_branch_name)

    assert (verdict.behind, verdict.ahead) == (0, 0)
    assert verdict.merge_conflict is False
    assert verdict.working_tree_overlap == []


def test_real_merge_conflict_is_reported_with_paths(
    fixture_repos_conflict_overlap, default_branch_name
):
    _origin, clone_path = fixture_repos_conflict_overlap

    verdict = _assess(clone_path, default_branch_name)

    assert verdict.merge_conflict is True
    assert verdict.conflict_paths == ["shared.txt"]


def test_branch_only_path_overlap_without_hunk_conflict_is_a_clean_merge(
    fixture_repos_incoming_rename, default_branch_name
):
    """Origin renames file.txt while we edit it: git merges that cleanly, so
    the verdict must not call it a conflict (the old path-overlap heuristic did)."""
    _origin, clone_path = fixture_repos_incoming_rename

    verdict = _assess(clone_path, default_branch_name)

    assert verdict.behind > 0 and verdict.ahead > 0
    assert verdict.merge_conflict is False


def test_uncommitted_edit_to_an_incoming_path_is_a_working_tree_overlap(
    fixture_repos_behind_no_overlap, default_branch_name
):
    _origin, clone_path = fixture_repos_behind_no_overlap
    (Path(clone_path) / "shared.txt").write_text("uncommitted local edit\n")

    verdict = _assess(clone_path, default_branch_name)

    assert verdict.working_tree_overlap == ["shared.txt"]
    assert verdict.merge_conflict is False  # merge-tree can't see uncommitted edits


def test_fetch_failure_is_data_not_a_guess(fixture_repos_fetch_fails, default_branch_name):
    _origin, clone_path = fixture_repos_fetch_fails

    verdict = _assess(clone_path, default_branch_name)

    assert verdict.fetch_ok is False
    assert verdict.fetch_error
    assert verdict.head_sha is None
    assert verdict.merge_conflict is None  # stale origin ref proves nothing


def test_unrelated_histories_are_undeterminable_not_clean(
    fixture_repos_no_common_ancestor, default_branch_name
):
    _origin, clone_path = fixture_repos_no_common_ancestor

    verdict = _assess(clone_path, default_branch_name)

    assert verdict.merge_conflict is None
    assert verdict.paths_determinable is False


def test_local_ref_is_honoured_instead_of_head(fixture_repos_conflict_overlap, default_branch_name):
    """The hook assesses the pushed SHA, not the checked-out HEAD."""
    _origin, clone_path = fixture_repos_conflict_overlap

    verdict = _assess(clone_path, default_branch_name, local_ref=f"origin/{default_branch_name}")

    assert (verdict.behind, verdict.ahead) == (0, 0)
    assert verdict.merge_conflict is False


def test_detailed_assessment_adds_incoming_commits_and_local_changes(
    fixture_repos_conflict_overlap, default_branch_name
):
    _origin, clone_path = fixture_repos_conflict_overlap

    verdict = _assess(clone_path, default_branch_name, detailed=True)

    assert [c.subject for c in verdict.incoming_commits] == ["origin advances shared.txt"]
    assert verdict.local_changes == {"shared.txt", "file.txt"}


def test_detailed_assessment_of_a_failed_fetch_stays_unfetched_and_undecided(
    fixture_repos_fetch_fails, default_branch_name
):
    """The push summary may still read the stale origin ref, but nothing may
    mistake that for a fresh answer: fetch_ok stays False, no head SHA, and
    merge_conflict is undeterminable rather than a (stale) clean/conflict."""
    _origin, clone_path = fixture_repos_fetch_fails

    verdict = _assess(clone_path, default_branch_name, detailed=True)

    assert verdict.fetch_ok is False
    assert verdict.head_sha is None
    assert verdict.merge_conflict is None


def _repo(clone_path, base, timeout=7):
    return RepoConfig(
        repo_path=clone_path, base_branches=[base], merge_tree_timeout_seconds=timeout
    )


def test_config_json_timeout_reaches_the_merge_tree_subprocess_via_check_repo(
    fixture_repos_conflict_overlap, default_branch_name, bbw_config_dir, merge_tree_stub
):
    _origin, clone_path = fixture_repos_conflict_overlap
    config.save_config(config.AppConfig(repos=[_repo(clone_path, default_branch_name, 7)]))
    repo = config.load_config().repos[0]
    recorded = merge_tree_stub(raise_timeout)

    status = base_verdict.check_repo(repo)

    assert recorded == [7]  # exactly one merge-tree call, with the config.json value
    branch = status.branch_statuses[0]
    assert branch.kind == StatusKind.CONFLICT_RISK  # path overlap still decides
    assert branch.reason == "conflict check timed out after 7s"


def test_assess_base_reports_a_timeout_as_timed_out_with_its_seconds(
    fixture_repos_conflict_overlap, default_branch_name, merge_tree_stub
):
    _origin, clone_path = fixture_repos_conflict_overlap
    merge_tree_stub(raise_timeout)

    verdict = _assess(clone_path, default_branch_name, merge_tree_timeout=9)

    assert verdict.merge_check == git_ops.TimedOut(seconds=9)
    assert verdict.merge_conflict is None
    assert verdict.conflict_paths == []


def test_timeout_with_undeterminable_paths_reports_timed_out_not_local_git_error(
    fixture_repos_conflict_overlap, default_branch_name, merge_tree_stub, monkeypatch
):
    _origin, clone_path = fixture_repos_conflict_overlap
    merge_tree_stub(raise_timeout)
    monkeypatch.setattr(git_ops, "merge_base", lambda *args, **kwargs: None)

    status = base_verdict.check_repo(_repo(clone_path, default_branch_name))

    assert status.branch_statuses[0].kind == StatusKind.CHECK_FAILED
    assert status.branch_statuses[0].reason == "conflict check timed out after 7s"


def test_timeout_with_determinable_paths_and_overlap_is_conflict_risk_noting_the_timeout(
    fixture_repos_conflict_overlap, default_branch_name, merge_tree_stub
):
    _origin, clone_path = fixture_repos_conflict_overlap
    merge_tree_stub(raise_timeout)

    status = base_verdict.check_repo(_repo(clone_path, default_branch_name))

    branch = status.branch_statuses[0]
    assert branch.kind == StatusKind.CONFLICT_RISK
    assert branch.conflict_paths == ["shared.txt"]
    assert branch.reason == "conflict check timed out after 7s"


def test_timeout_with_determinable_paths_and_no_overlap_is_diverged_noting_the_timeout(
    fixture_repos_diverged, default_branch_name, merge_tree_stub
):
    _origin, clone_path = fixture_repos_diverged
    merge_tree_stub(raise_timeout)

    status = base_verdict.check_repo(_repo(clone_path, default_branch_name))

    branch = status.branch_statuses[0]
    assert branch.kind == StatusKind.DIVERGED
    assert branch.reason == "conflict check timed out after 7s"


def test_a_decisive_merge_tree_answer_carries_no_timeout_note(
    fixture_repos_diverged, default_branch_name
):
    _origin, clone_path = fixture_repos_diverged

    status = base_verdict.check_repo(_repo(clone_path, default_branch_name))

    assert status.branch_statuses[0].kind == StatusKind.DIVERGED
    assert status.branch_statuses[0].reason is None


def test_non_timeout_merge_tree_failure_still_reports_local_git_error(
    fixture_repos_conflict_overlap, default_branch_name, merge_tree_stub, monkeypatch
):
    import subprocess

    _origin, clone_path = fixture_repos_conflict_overlap
    merge_tree_stub(lambda args, timeout: subprocess.CompletedProcess(args, 128, b"", b"fatal"))
    monkeypatch.setattr(git_ops, "merge_base", lambda *args, **kwargs: None)

    status = base_verdict.check_repo(_repo(clone_path, default_branch_name))

    assert status.branch_statuses[0].reason == "conflict check failed - local git error"


def test_timeout_is_logged_as_a_warning(
    fixture_repos_diverged, default_branch_name, bbw_config_dir, merge_tree_stub
):
    _origin, clone_path = fixture_repos_diverged
    merge_tree_stub(raise_timeout)

    base_verdict.check_repo(_repo(clone_path, default_branch_name))

    logged = log.log_path().read_text()
    assert "[WARN] merge-tree timed out after 7s" in logged
    assert clone_path in logged
    assert default_branch_name in logged


def test_timeout_warning_is_logged_once_per_state_change_not_every_poll(
    fixture_repos_diverged, default_branch_name, bbw_config_dir, merge_tree_stub
):
    _origin, clone_path = fixture_repos_diverged
    merge_tree_stub(raise_timeout)
    repo = _repo(clone_path, default_branch_name)

    for _ in range(3):
        base_verdict.check_repo(repo)

    assert log.log_path().read_text().count("[WARN] merge-tree timed out") == 1

    # A different state (new timeout value) is a change: logged again.
    base_verdict.check_repo(_repo(clone_path, default_branch_name, timeout=30))
    assert log.log_path().read_text().count("[WARN] merge-tree timed out") == 2


def test_timeout_warning_returns_after_a_decisive_answer_in_between(
    fixture_repos_diverged, default_branch_name, bbw_config_dir, monkeypatch
):
    _origin, clone_path = fixture_repos_diverged
    repo = _repo(clone_path, default_branch_name)
    answers = iter([git_ops.TimedOut(7), git_ops.Clean(), git_ops.TimedOut(7)])
    monkeypatch.setattr(git_ops, "merge_tree_check", lambda *args, **kwargs: next(answers))

    for _ in range(3):
        base_verdict.check_repo(repo)

    assert log.log_path().read_text().count("[WARN] merge-tree timed out") == 2


def test_undeterminable_cause_is_logged(
    fixture_repos_diverged, default_branch_name, bbw_config_dir, merge_tree_stub
):
    import subprocess

    _origin, clone_path = fixture_repos_diverged
    merge_tree_stub(
        lambda args, timeout: subprocess.CompletedProcess(args, 128, b"", b"fatal: bad object\n")
    )

    base_verdict.check_repo(_repo(clone_path, default_branch_name))

    assert "merge-tree could not check" in log.log_path().read_text()
    assert "fatal: bad object" in log.log_path().read_text()


def test_assess_base_alone_never_writes_the_app_log(
    fixture_repos_diverged, default_branch_name, bbw_config_dir, merge_tree_stub
):
    """The pre-push hook calls assess_base; only the menu path (check_repo) may log."""
    _origin, clone_path = fixture_repos_diverged
    merge_tree_stub(raise_timeout)

    _assess(clone_path, default_branch_name, detailed=True)

    assert not log.log_path().exists()


def _timed_out_warns() -> int:
    return log.log_path().read_text().count("[WARN] merge-tree timed out")


def test_timeout_warning_returns_after_a_fetch_failure_in_between(bbw_config_dir):
    timed_out = base_verdict.BaseVerdict(
        base="main", fetch_ok=True, merge_check=git_ops.TimedOut(7)
    )
    fetch_failed = base_verdict.BaseVerdict(base="main", fetch_ok=False, fetch_error="down")

    for verdict in (timed_out, timed_out, fetch_failed, timed_out):
        base_verdict._branch_status("/r", verdict)

    assert _timed_out_warns() == 2


def test_timeout_warning_returns_after_a_comparison_failure_in_between(bbw_config_dir):
    timed_out = base_verdict.BaseVerdict(
        base="main", fetch_ok=True, merge_check=git_ops.TimedOut(7)
    )
    comparison_failed = base_verdict.BaseVerdict(base="main", fetch_ok=True, comparison_ok=False)

    for verdict in (timed_out, comparison_failed, timed_out):
        base_verdict._branch_status("/r", verdict)

    assert _timed_out_warns() == 2


def test_timeout_warning_returns_after_an_undeterminable_with_empty_cause(bbw_config_dir):
    timed_out = base_verdict.BaseVerdict(
        base="main", fetch_ok=True, merge_check=git_ops.TimedOut(7)
    )
    unknown = base_verdict.BaseVerdict(
        base="main", fetch_ok=True, merge_check=git_ops.Undeterminable()
    )

    for verdict in (timed_out, unknown, timed_out):
        base_verdict._branch_status("/r", verdict)

    assert _timed_out_warns() == 2


def test_logged_merge_checks_are_pruned_for_repos_and_bases_no_longer_configured(
    bbw_config_dir,
):
    timed_out = base_verdict.BaseVerdict(
        base="main", fetch_ok=True, merge_check=git_ops.TimedOut(7)
    )
    for repo_path, base in (("/keep", "main"), ("/keep", "gone"), ("/removed", "main")):
        base_verdict._branch_status(
            repo_path, base_verdict.BaseVerdict(**{**timed_out.__dict__, "base": base})
        )

    base_verdict.prune_logged_merge_checks([RepoConfig(repo_path="/keep", base_branches=["main"])])

    assert set(base_verdict._logged_merge_checks) == {("/keep", "main")}

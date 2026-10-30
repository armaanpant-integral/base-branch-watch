from __future__ import annotations

import base_branch_watch.app.panel_model as panel_model_module
from base_branch_watch.app.panel_model import PillTone, build_panel
from base_branch_watch.core.models import (
    BranchStatus,
    PrStatus,
    PrStatusKind,
    RepoStatus,
    Severity,
    StaleBranch,
    StaleKind,
    StatusKind,
)


def _repo(name="repo-a", unpushed=0, branches=None, failure_reason=None, current_branch="main"):
    return RepoStatus(
        repo_path=f"/tmp/{name}",
        name=name,
        current_branch=current_branch,
        unpushed=unpushed,
        branch_statuses=branches if branches is not None else [],
        failure_reason=failure_reason,
    )


def _branch(kind=StatusKind.UP_TO_DATE, base="main", behind=0, conflict_paths=()):
    return BranchStatus(
        base=base,
        behind=behind,
        ahead_of_base=0,
        kind=kind,
        conflict_paths=list(conflict_paths),
    )


def _open_pr(number=412, **kwargs):
    defaults = dict(
        checks_pass=3,
        checks_total=3,
        review_decision="APPROVED",
        merge_state_status="CLEAN",
    )
    defaults.update(kwargs)
    return PrStatus(kind=PrStatusKind.OPEN, number=number, **defaults)


def _stale(count):
    return [StaleBranch(name=f"old-{i}", kind=StaleKind.MERGED) for i in range(count)]


def test_up_to_date_repo_with_ready_pr_is_ok_tile():
    status = _repo("codex-drm-ui", branches=[_branch()])

    panel = build_panel([status], {status.repo_path: _open_pr(412)}, {})

    (tile,) = panel.tiles
    assert tile.name == "codex-drm-ui"
    assert tile.base == "main"
    assert (tile.behind, tile.unpushed) == (0, 0)
    assert tile.severity == Severity.OK
    assert tile.pill == "PR ready"
    assert tile.pill_tone == PillTone.OK
    assert tile.detail == "PR #412"


def test_conflict_risk_tile_names_file_count_and_is_blocking():
    status = _repo(
        "UIGateway",
        branches=[
            _branch(
                StatusKind.CONFLICT_RISK,
                base="development",
                behind=3,
                conflict_paths=["a.py", "b.py"],
            )
        ],
    )

    (tile,) = build_panel([status], {}, {}).tiles

    assert tile.pill == "Conflict · 2 files"
    assert tile.pill_tone == PillTone.ATTENTION
    assert tile.severity == Severity.BLOCKING
    assert (tile.base, tile.behind) == ("development", 3)


def test_conflict_pill_singular_for_one_file():
    status = _repo("r", branches=[_branch(StatusKind.CONFLICT_RISK, conflict_paths=["a.py"])])

    (tile,) = build_panel([status], {}, {}).tiles

    assert tile.pill == "Conflict · 1 file"


def test_unpushed_count_comes_from_repo_level_axis():
    status = _repo("fxi", unpushed=2, branches=[_branch(StatusKind.BEHIND, behind=1)])

    (tile,) = build_panel([status], {}, {}).tiles

    assert (tile.behind, tile.unpushed) == (1, 2)
    assert tile.pill == "Behind base"
    assert tile.severity == Severity.ATTENTION


def test_behind_is_the_max_across_multiple_bases():
    status = _repo(
        "multi",
        branches=[
            _branch(StatusKind.BEHIND, base="main", behind=2),
            _branch(StatusKind.BEHIND, base="release", behind=9),
        ],
    )

    (tile,) = build_panel([status], {}, {}).tiles

    assert tile.behind == 9


def test_check_failed_repo_has_check_failed_pill_and_no_base():
    status = _repo("broken", failure_reason="fetch failed")

    (tile,) = build_panel([status], {}, {}).tiles

    assert tile.pill == "Check failed"
    assert tile.pill_tone == PillTone.ATTENTION
    assert tile.base == ""


def test_not_yet_checked_repo_is_neutral():
    status = _repo("new")

    (tile,) = build_panel([status], {}, {}).tiles

    assert tile.pill == "Checking…"
    assert tile.pill_tone == PillTone.NEUTRAL
    assert tile.severity == Severity.OK


def test_no_pr_is_neutral_pill_and_detail_falls_back_to_stale_count():
    status = _repo("crypto-trading", branches=[_branch()])
    pr = PrStatus(kind=PrStatusKind.NO_PR, current_branch="main")

    (tile,) = build_panel([status], {status.repo_path: pr}, {status.repo_path: _stale(13)}).tiles

    assert tile.pill == "No open PR"
    assert tile.pill_tone == PillTone.NEUTRAL
    assert tile.detail == "13 stale"


def test_detail_is_empty_when_no_pr_number_and_no_stale():
    status = _repo("quiet", branches=[_branch()])

    (tile,) = build_panel([status], {}, {}).tiles

    assert tile.detail == ""


def test_open_pr_with_failing_checks_surfaces_worst_state_as_attention_pill():
    status = _repo("r", branches=[_branch()])
    pr = _open_pr(7, checks_pass=8, checks_fail=3, checks_total=11)

    (tile,) = build_panel([status], {status.repo_path: pr}, {}).tiles

    assert tile.pill == "3 failing (8/11)"
    assert tile.pill_tone == PillTone.ATTENTION
    # PR state is informational: it never changes the git-status severity.
    assert tile.severity == Severity.OK


def test_merged_pr_is_neutral():
    status = _repo("r", branches=[_branch()])
    pr = PrStatus(kind=PrStatusKind.MERGED, number=41)

    (tile,) = build_panel([status], {status.repo_path: pr}, {}).tiles

    assert tile.pill == "PR merged"
    assert tile.pill_tone == PillTone.NEUTRAL
    assert tile.detail == "PR #41"


def test_pr_check_failed_is_neutral_unavailable():
    status = _repo("r", branches=[_branch()])
    pr = PrStatus.failed("boom")

    (tile,) = build_panel([status], {status.repo_path: pr}, {}).tiles

    assert tile.pill == "PR status unavailable"
    assert tile.pill_tone == PillTone.NEUTRAL


def test_tiles_sort_attention_first_worst_severity_then_name():
    ok_b = _repo("b-ok", branches=[_branch()])
    ok_a = _repo("a-ok", branches=[_branch()])
    behind = _repo("z-behind", branches=[_branch(StatusKind.BEHIND, behind=1)])
    conflict = _repo("y-conflict", branches=[_branch(StatusKind.CONFLICT_RISK)])

    panel = build_panel([ok_b, behind, ok_a, conflict], {}, {})

    assert [t.name for t in panel.tiles] == ["y-conflict", "z-behind", "a-ok", "b-ok"]


def test_counts_for_summary_and_tabs():
    conflict = _repo("c", branches=[_branch(StatusKind.CONFLICT_RISK)])
    ok = _repo("o", branches=[_branch()])
    prs = {ok.repo_path: _open_pr(1)}
    stale = {conflict.repo_path: _stale(2), ok.repo_path: _stale(3)}

    panel = build_panel([conflict, ok], prs, stale)

    assert panel.attention_count == 1
    assert panel.clear_count == 1
    assert panel.open_pr_count == 1
    assert panel.stale_total == 5


def test_empty_panel_has_no_tiles_and_zero_counts():
    panel = build_panel([], {}, {})

    assert panel.tiles == []
    assert (panel.attention_count, panel.clear_count) == (0, 0)


def test_module_is_pure_no_appkit_or_rumps_import():
    source = open(panel_model_module.__file__, encoding="utf-8").read()

    assert "rumps" not in source
    assert "AppKit" not in source
    assert "objc" not in source

from __future__ import annotations

from base_branch_watch.app import menu_builder
from base_branch_watch.core.models import (
    _KIND_SEVERITY,
    _WORST_KIND_RANK,
    BranchStatus,
    PrStatus,
    PrStatusKind,
    RepoStatus,
    StatusKind,
)


def _status(
    name="repo-a",
    unpushed=0,
    branch_statuses=None,
    failure_reason=None,
):
    return RepoStatus(
        repo_path=f"/tmp/{name}",
        name=name,
        current_branch="main",
        unpushed=unpushed,
        branch_statuses=branch_statuses if branch_statuses is not None else [],
        failure_reason=failure_reason,
    )


def _up_to_date_status(name="repo-a"):
    return _status(
        name,
        branch_statuses=[
            BranchStatus(base="main", behind=0, ahead_of_base=0, kind=StatusKind.UP_TO_DATE)
        ],
    )


def _behind_status(name="repo-b", behind=3, base="main"):
    return _status(
        name,
        branch_statuses=[
            BranchStatus(base=base, behind=behind, ahead_of_base=0, kind=StatusKind.BEHIND)
        ],
    )


def _unpushed_only_status(name="repo-e", unpushed=2, base="main"):
    return _status(
        name,
        unpushed=unpushed,
        branch_statuses=[
            BranchStatus(base=base, behind=0, ahead_of_base=0, kind=StatusKind.UP_TO_DATE)
        ],
    )


def _behind_and_unpushed_status(name="repo-f", behind=2, unpushed=1, base="main"):
    return _status(
        name,
        unpushed=unpushed,
        branch_statuses=[
            BranchStatus(base=base, behind=behind, ahead_of_base=0, kind=StatusKind.BEHIND)
        ],
    )


def _diverged_status(name="repo-g", behind=3, ahead=2, base="main"):
    return _status(
        name,
        branch_statuses=[
            BranchStatus(base=base, behind=behind, ahead_of_base=ahead, kind=StatusKind.DIVERGED)
        ],
    )


def _conflict_risk_status(name="repo-i", conflict_paths=("a.py", "b.py"), base="main"):
    return _status(
        name,
        branch_statuses=[
            BranchStatus(
                base=base,
                behind=1,
                ahead_of_base=0,
                kind=StatusKind.CONFLICT_RISK,
                conflict_paths=list(conflict_paths),
            )
        ],
    )


def _not_checked_status(name="repo-c"):
    return _status(name)


def _check_failed_status(name="repo-d", reason="fetch failed — check network/SSH access"):
    return _status(name, failure_reason=reason)


def _check_failed_single_base_status(
    name="repo-h", reason="fetch failed — check network/SSH access", base="main"
):
    return _status(
        name,
        branch_statuses=[
            BranchStatus(
                base=base, behind=0, ahead_of_base=0, kind=StatusKind.CHECK_FAILED, reason=reason
            )
        ],
    )


def _multi_base_status(name="repo-multi", unpushed=0):
    return _status(
        name,
        unpushed=unpushed,
        branch_statuses=[
            BranchStatus(base="main", behind=0, ahead_of_base=0, kind=StatusKind.UP_TO_DATE),
            BranchStatus(base="release", behind=4, ahead_of_base=1, kind=StatusKind.DIVERGED),
        ],
    )


def test_build_empty_state_when_no_repos():
    specs = menu_builder.build([], has_repos=False)

    assert len(specs) == 1
    assert specs[0].title == "No repos watched — click Add Repo… below"
    assert specs[0].callback_key is None


def test_build_up_to_date_row():
    specs = menu_builder.build([_up_to_date_status("myrepo")], has_repos=True)

    assert specs[0].title == "🟢 myrepo"


def test_build_behind_row_is_clickable():
    status = _behind_status("myrepo", behind=3, base="main")
    specs = menu_builder.build([status], has_repos=True)

    assert specs[0].title == "🟡 myrepo: 3 behind (main)"
    assert specs[0].callback_key is not None


def test_build_unpushed_only_row():
    status = _unpushed_only_status("myrepo", unpushed=2)
    specs = menu_builder.build([status], has_repos=True)

    assert specs[0].title == "🟡 myrepo: 2 unpushed"
    assert specs[0].callback_key is not None


def test_build_behind_and_unpushed_row():
    status = _behind_and_unpushed_status("myrepo", behind=2, unpushed=1, base="main")
    specs = menu_builder.build([status], has_repos=True)

    assert specs[0].title == "🟡 myrepo: 2 behind (main) · 1 unpushed"


def test_build_diverged_row():
    status = _diverged_status("myrepo", behind=3, ahead=2, base="main")
    specs = menu_builder.build([status], has_repos=True)

    assert specs[0].title == "🔴 myrepo: diverged — 3 behind, 2 ahead (main)"


def test_build_conflict_risk_row_shows_badge():
    status = _conflict_risk_status("myrepo", conflict_paths=("a.py", "b.py"), base="main")
    specs = menu_builder.build([status], has_repos=True)

    assert specs[0].title == "⚠️ myrepo: conflict risk — 2 file(s) overlap (main)"


def test_build_conflict_risk_lists_overlapping_paths():
    status = _conflict_risk_status("myrepo", conflict_paths=("a.py", "b.py"), base="main")
    specs = menu_builder.build([status], has_repos=True)

    assert specs[0].callback_key is None
    assert [c.title for c in specs[0].children] == ["a.py", "b.py"]


def test_build_conflict_risk_caps_path_rows():
    paths = [f"file{i:02d}.py" for i in range(15)]
    status = _conflict_risk_status("myrepo", conflict_paths=paths, base="main")
    specs = menu_builder.build([status], has_repos=True)

    children = specs[0].children
    assert len(children) == 11
    assert [c.title for c in children[:10]] == paths[:10]
    assert children[-1].title == "…and 5 more"


def test_build_multi_base_conflict_risk_child_row_has_path_children():
    status = _status(
        "myrepo",
        branch_statuses=[
            BranchStatus(base="main", behind=0, ahead_of_base=0, kind=StatusKind.UP_TO_DATE),
            BranchStatus(
                base="release",
                behind=1,
                ahead_of_base=0,
                kind=StatusKind.CONFLICT_RISK,
                conflict_paths=["a.py", "b.py"],
            ),
        ],
    )
    specs = menu_builder.build([status], has_repos=True)

    parent = specs[0]
    conflict_child = next(c for c in parent.children if c.title.startswith("⚠️"))
    assert [gc.title for gc in conflict_child.children] == ["a.py", "b.py"]
    assert conflict_child.callback_key is None


def test_build_check_failed_row_repo_level():
    specs = menu_builder.build([_check_failed_status("myrepo")], has_repos=True)

    assert specs[0].title == "🔴 myrepo: check failed — fetch failed — check network/SSH access"


def test_build_check_failed_row_single_base_level():
    specs = menu_builder.build([_check_failed_single_base_status("myrepo")], has_repos=True)

    assert specs[0].title == "🔴 myrepo: check failed — fetch failed — check network/SSH access"


def test_build_not_checked_row_uses_ellipsis_and_is_not_clickable():
    specs = menu_builder.build([_not_checked_status("myrepo")], has_repos=True)

    assert specs[0].title == "… myrepo"
    assert specs[0].callback_key is None


def test_build_multi_base_row_has_no_repo_name_no_unpushed_children_no_parent_callback():
    status = _multi_base_status("myrepo")
    specs = menu_builder.build([status], has_repos=True)

    parent = specs[0]
    assert parent.title == "🔴 myrepo (2 base branches)"
    assert parent.callback_key is None
    assert len(parent.children) == 2

    main_child = next(c for c in parent.children if c.title.startswith("🟢"))
    release_child = next(c for c in parent.children if c.title.startswith("🔴"))
    assert main_child.title == "🟢 main"
    assert release_child.title == "🔴 release: diverged — 4 behind, 1 ahead"
    assert main_child.callback_key is not None
    assert release_child.callback_key is not None


def test_build_multi_base_row_appends_unpushed_suffix_when_nonzero():
    status = _multi_base_status("myrepo", unpushed=3)
    specs = menu_builder.build([status], has_repos=True)

    assert specs[0].title == "🔴 myrepo (2 base branches) · 3 unpushed"
    # Unpushed never duplicated onto children.
    for child in specs[0].children:
        assert "unpushed" not in child.title


def test_title_for_up_to_date_only():
    assert menu_builder.title_for([_up_to_date_status()]) == "🟢"


def test_title_for_behind():
    assert menu_builder.title_for([_behind_status()]) == "🟡 1"


def test_title_for_check_failed():
    assert menu_builder.title_for([_check_failed_status()]) == "🔴 1"


def test_title_for_diverged():
    assert menu_builder.title_for([_diverged_status()]) == "🔴 1"


def test_title_for_worst_status_wins_across_repos():
    title = menu_builder.title_for([_up_to_date_status("a"), _diverged_status("b")])

    assert title == "🔴 1"


def test_menu_builder_module_is_pure_no_rumps_or_appkit_import():
    import base_branch_watch.app.menu_builder as mb_module

    source = open(mb_module.__file__).read()
    assert "import rumps" not in source
    assert "from rumps" not in source
    assert "import AppKit" not in source
    assert "from AppKit" not in source


# -- D-08 regression guard: PrStatusKind must never fold into the severity
# system (_KIND_SEVERITY / _WORST_KIND_RANK are StatusKind-only). ------------


def test_severity_tables_contain_no_pr_status_kind_members():
    assert not any(isinstance(k, PrStatusKind) for k in _KIND_SEVERITY)
    assert not any(isinstance(k, PrStatusKind) for k in _WORST_KIND_RANK)


# -- PR row (D-05/D-06/D-07/D-02) — RED until Task 3 implements _pr_row -----


def _pr_status(
    kind=PrStatusKind.OPEN,
    number=42,
    checks_pass=3,
    checks_fail=0,
    checks_pending=0,
    checks_total=3,
    review_decision="APPROVED",
    merge_state_status="CLEAN",
    base_ref="main",
    current_branch=None,
    reason=None,
):
    return PrStatus(
        kind=kind,
        number=number,
        checks_pass=checks_pass,
        checks_fail=checks_fail,
        checks_pending=checks_pending,
        checks_total=checks_total,
        review_decision=review_decision,
        merge_state_status=merge_state_status,
        base_ref=base_ref,
        current_branch=current_branch,
        reason=reason,
    )


def test_pr_row_open_all_clear_locked_string():
    status = _pr_status(
        kind=PrStatusKind.OPEN,
        number=42,
        checks_pass=3,
        checks_total=3,
        review_decision="APPROVED",
        merge_state_status="CLEAN",
    )

    spec = menu_builder._pr_row(status, "base-branch-watch")

    assert spec.title == (
        "🔀 base-branch-watch: PR #42 — ✅ 3/3 checks · ✅ approved · ✅ mergeable"
    )
    assert spec.callback_key is None
    assert len(spec.children) == 3


def test_pr_row_no_pr_locked_string():
    status = _pr_status(kind=PrStatusKind.NO_PR, current_branch="main")

    spec = menu_builder._pr_row(status, "base-branch-watch")

    assert spec.title == "⚪ base-branch-watch: no open PR (main)"
    assert spec.callback_key is None
    assert spec.children == []


def test_pr_row_open_all_clear_children_order_and_text():
    status = _pr_status(
        kind=PrStatusKind.OPEN,
        number=42,
        checks_pass=3,
        checks_total=3,
        review_decision="APPROVED",
        merge_state_status="CLEAN",
    )

    spec = menu_builder._pr_row(status, "base-branch-watch")

    assert [c.title for c in spec.children] == [
        "✅ Checks: 3/3 passing",
        "✅ Review: approved",
        "✅ Mergeable: yes",
    ]
    for child in spec.children:
        assert child.callback_key is None


def test_pr_row_open_failing_checks_locked_string():
    status = _pr_status(
        kind=PrStatusKind.OPEN,
        number=7,
        checks_pass=8,
        checks_fail=3,
        checks_total=11,
        review_decision="REVIEW_REQUIRED",
        merge_state_status="DIRTY",
        base_ref="main",
    )

    spec = menu_builder._pr_row(status, "base-branch-watch")

    assert spec.title == (
        "🔀 base-branch-watch: PR #7 — ❌ 3 failing (8/11) · ⏳ review pending · ❌ conflicts"
    )
    assert [c.title for c in spec.children] == [
        "❌ Checks: 3 failing (8/11)",
        "⏳ Review: pending",
        "❌ Mergeable: conflicts with main",
    ]


def test_pr_row_open_no_checks_configured():
    status = _pr_status(
        kind=PrStatusKind.OPEN,
        checks_pass=0,
        checks_fail=0,
        checks_pending=0,
        checks_total=0,
    )

    spec = menu_builder._pr_row(status, "base-branch-watch")

    assert "— no checks ·" in spec.title
    assert spec.children[0].title == "— Checks: no checks configured"


def test_pr_row_open_checks_fetch_unavailable_is_distinct_from_no_checks():
    """WR-04 regression: checks_total == -1 (core.pr_status's invocation-
    failure sentinel) must render as "unavailable", never silently claim
    "no checks configured" like the genuine checks_total == 0 case."""
    status = _pr_status(
        kind=PrStatusKind.OPEN,
        checks_pass=0,
        checks_fail=0,
        checks_pending=0,
        checks_total=-1,
    )

    spec = menu_builder._pr_row(status, "base-branch-watch")

    assert "checks unavailable" in spec.title
    assert "no checks configured" not in spec.title
    assert spec.children[0].title == "⚠️ Checks: checks unavailable — fetch failed"


def test_pr_row_no_pr_falls_back_to_detached_head_placeholder():
    """IN-02 regression: a falsy current_branch must not render empty
    parens."""
    status = _pr_status(kind=PrStatusKind.NO_PR, current_branch=None)

    spec = menu_builder._pr_row(status, "base-branch-watch")

    assert spec.title == "⚪ base-branch-watch: no open PR (detached HEAD)"


def test_pr_row_open_pending_checks():
    status = _pr_status(
        kind=PrStatusKind.OPEN,
        checks_pass=1,
        checks_fail=0,
        checks_pending=2,
        checks_total=3,
    )

    spec = menu_builder._pr_row(status, "base-branch-watch")

    assert "⏳ 2 pending (1/3) ·" in spec.title
    assert spec.children[0].title == "⏳ Checks: 2 pending (1/3)"


def test_pr_row_open_null_review_decision_is_distinct_case():
    status = _pr_status(kind=PrStatusKind.OPEN, review_decision=None)

    spec = menu_builder._pr_row(status, "base-branch-watch")

    assert "— no review required ·" in spec.title
    assert spec.children[1].title == "— Review: not required"


def test_pr_row_open_mergeable_state_table():
    cases = {
        "CLEAN": ("✅", "mergeable", "yes"),
        "HAS_HOOKS": ("✅", "mergeable", "yes"),
        "DIRTY": ("❌", "conflicts", "conflicts with main"),
        "BLOCKED": ("❌", "blocked (required checks)", "blocked — required checks not met"),
        "BEHIND": ("⏳", "behind base", "behind main"),
        "DRAFT": ("⏳", "draft", "draft PR"),
        "UNSTABLE": ("⏳", "mergeable (optional check failing)", "yes (optional check failing)"),
        "UNKNOWN": ("⏳", "mergeability unknown", "unknown — GitHub still computing"),
        "SOME_FUTURE_VALUE": (
            "⏳",
            "mergeability unknown",
            "unknown — GitHub still computing",
        ),
    }
    for value, (glyph, text, child_text) in cases.items():
        status = _pr_status(kind=PrStatusKind.OPEN, merge_state_status=value, base_ref="main")
        spec = menu_builder._pr_row(status, "base-branch-watch")
        assert f"{glyph} {text}" in spec.title, value
        assert spec.children[2].title == f"{glyph} Mergeable: {child_text}", value


def test_pr_row_check_failed_locked_string():
    status = _pr_status(kind=PrStatusKind.CHECK_FAILED, reason="gh pr view timed out after 15s")

    spec = menu_builder._pr_row(status, "base-branch-watch")

    assert spec.title == (
        "⚠️ base-branch-watch: PR status unavailable — gh pr view timed out after 15s"
    )
    assert spec.callback_key is None
    assert spec.children == []


def test_pr_row_no_pr_truncates_long_branch_name_to_40_chars():
    long_branch = "x" * 60
    status = _pr_status(kind=PrStatusKind.NO_PR, current_branch=long_branch)

    spec = menu_builder._pr_row(status, "base-branch-watch")

    branch_segment = spec.title.split("(", 1)[1].rstrip(")")
    assert len(branch_segment) <= 41
    assert branch_segment.endswith("…")


def test_pr_row_check_failed_truncates_long_reason_to_50_chars():
    long_reason = "y" * 80
    status = _pr_status(kind=PrStatusKind.CHECK_FAILED, reason=long_reason)

    spec = menu_builder._pr_row(status, "base-branch-watch")

    reason_segment = spec.title.split("— ", 1)[1]
    assert len(reason_segment) <= 51
    assert reason_segment.endswith("…")


# -- Task 2 (Plan 02): remaining PrStatusKind rows — locked UI-SPEC strings --


def test_pr_row_not_installed_locked_string():
    status = _pr_status(kind=PrStatusKind.NOT_INSTALLED)

    spec = menu_builder._pr_row(status, "repo")

    assert spec.title == "⚠️ repo: PR status — gh not installed"
    assert spec.callback_key is None
    assert spec.children == []


def test_pr_row_not_authenticated_locked_string():
    status = _pr_status(kind=PrStatusKind.NOT_AUTHENTICATED)

    spec = menu_builder._pr_row(status, "repo")

    assert spec.title == "⚠️ repo: PR status — run gh auth login"
    assert spec.callback_key is None
    assert spec.children == []


def test_pr_row_rate_limited_with_retry_at_locked_string():
    status = PrStatus(kind=PrStatusKind.RATE_LIMITED, retry_at="14:30")

    spec = menu_builder._pr_row(status, "repo")

    assert spec.title == "⚠️ repo: PR status — rate limited, retrying at 14:30"
    assert spec.callback_key is None
    assert spec.children == []


def test_pr_row_rate_limited_without_retry_at_omits_gracefully():
    status = PrStatus(kind=PrStatusKind.RATE_LIMITED, retry_at=None)

    spec = menu_builder._pr_row(status, "repo")

    assert spec.title == "⚠️ repo: PR status — rate limited"
    assert "None" not in spec.title
    assert "at " not in spec.title


def test_pr_row_merged_locked_string():
    status = PrStatus(kind=PrStatusKind.MERGED, number=42)

    spec = menu_builder._pr_row(status, "repo")

    assert spec.title == "✅ repo: PR #42 merged"
    assert spec.callback_key is None
    assert spec.children == []


def test_pr_row_closed_locked_string():
    status = PrStatus(kind=PrStatusKind.CLOSED, number=7)

    spec = menu_builder._pr_row(status, "repo")

    assert spec.title == "⚫ repo: PR #7 closed (not merged)"
    assert spec.callback_key is None
    assert spec.children == []

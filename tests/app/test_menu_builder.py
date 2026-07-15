from __future__ import annotations

from base_branch_watch.app import menu_builder
from base_branch_watch.core.models import (
    BranchStatus,
    PrStatus,
    PrStatusKind,
    RepoStatus,
    StatusKind,
    _KIND_SEVERITY,
    _WORST_KIND_RANK,
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

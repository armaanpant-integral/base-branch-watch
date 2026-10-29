"""Stale-branch menu row: pure StaleBranch list -> MenuItemSpec, no rumps."""

from __future__ import annotations

from base_branch_watch.app import menu_builder
from base_branch_watch.core.models import StaleBranch, StaleKind


def _gone_merged(name: str) -> StaleBranch:
    return StaleBranch(name=name, kind=StaleKind.GONE_MERGED)


def test_row_counts_the_stale_branches_and_lists_each_with_its_reason():
    row = menu_builder.stale_row([_gone_merged("feat/x"), _gone_merged("feat/y")], "repo-a")

    assert row.title == "🧹 repo-a: 2 stale branches"
    assert row.callback_key is None
    assert [child.title for child in row.children] == [
        "feat/x - remote deleted, merged",
        "feat/y - remote deleted, merged",
    ]


def test_a_single_stale_branch_reads_as_singular():
    row = menu_builder.stale_row([_gone_merged("feat/x")], "repo-a")

    assert row.title == "🧹 repo-a: 1 stale branch"


def test_nothing_stale_means_no_row_at_all():
    assert menu_builder.stale_row([], "repo-a") is None


def test_each_kind_gets_its_own_label_and_unmerged_warns_before_deleting():
    row = menu_builder.stale_row(
        [
            StaleBranch(name="a", kind=StaleKind.GONE_MERGED),
            StaleBranch(name="b", kind=StaleKind.GONE_UNMERGED),
            StaleBranch(name="c", kind=StaleKind.MERGED),
        ],
        "repo-a",
    )

    assert [child.title for child in row.children] == [
        "a - remote deleted, merged",
        "b - remote deleted, NOT merged (check before deleting)",
        "c - merged, remote branch still exists",
    ]

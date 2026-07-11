"""Unit tests for the pure D-05/D-06 drift summary builder.

No git/subprocess here -- IncomingCommit is a plain dataclass, so these
tests construct instances directly (mirrors app/test_menu_builder.py's
statuses -> list[MenuItemSpec] convention). Assertions run against the
returned lines, never stdout -- build_summary is pure, hook.py owns the
terminal output.
"""

from __future__ import annotations

from base_branch_watch.core.hook_summary import build_summary
from base_branch_watch.core.models import IncomingCommit


def _commit(short_hash: str, subject: str, changed_paths: set[str] | None = None) -> IncomingCommit:
    return IncomingCommit(
        short_hash=short_hash,
        author="Jane",
        subject=subject,
        changed_paths=changed_paths or set(),
    )


def test_build_summary_lists_commits():
    commits = [
        _commit("a1b2c3d", "fix retry timeout"),
        _commit("e4f5a6b", "add logging"),
    ]

    lines = build_summary([("origin/main", commits)], overlap_paths=set())

    joined = "\n".join(lines)
    assert "origin/main" in joined
    assert "a1b2c3d Jane: fix retry timeout" in joined
    assert "e4f5a6b Jane: add logging" in joined


def test_build_summary_caps_at_15_with_overflow_tail():
    commits = [_commit(f"{i:07x}", f"commit {i}") for i in range(20)]

    lines = build_summary([("origin/main", commits)], overlap_paths=set())

    commit_lines = [line for line in lines if "Jane:" in line]
    assert len(commit_lines) == 15
    assert "…and 5 more" in lines


def test_build_summary_flags_overlapping_commits():
    overlapping = _commit("1111111", "touches shared.txt", changed_paths={"shared.txt"})
    clean = _commit("2222222", "touches other.txt", changed_paths={"other.txt"})

    lines = build_summary(
        [("origin/main", [overlapping, clean])], overlap_paths={"shared.txt"}
    )

    overlap_line = next(line for line in lines if "1111111" in line)
    clean_line = next(line for line in lines if "2222222" in line)
    assert "⚠️" in overlap_line
    assert "⚠️" not in clean_line


def test_build_summary_groups_multiple_bases():
    main_commits = [_commit("aaaaaaa", "main change")]
    release_commits = [_commit("bbbbbbb", "release change")]

    lines = build_summary(
        [("origin/main", main_commits), ("origin/release", release_commits)],
        overlap_paths=set(),
    )

    header_lines = [line for line in lines if "incoming commit" in line]
    assert len(header_lines) == 2
    assert any("origin/main" in line for line in header_lines)
    assert any("origin/release" in line for line in header_lines)
    joined = "\n".join(lines)
    assert "aaaaaaa" in joined
    assert "bbbbbbb" in joined

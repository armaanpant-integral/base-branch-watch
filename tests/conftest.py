"""Shared pytest fixtures: isolated config dir + real fixture git repos.

No mocking of git itself — tests exercise real tmp git repos per
ARCHITECTURE.md's Suggested Build Order guidance.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

GIT = "/usr/bin/git"


def _run(args: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        [GIT, "-C", str(cwd), *args],
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 0, f"git {args} failed: {result.stderr}"
    return result


@pytest.fixture()
def bbw_config_dir(tmp_path, monkeypatch):
    """Isolate config_dir()/log dir to a per-test tmp directory."""
    cfg_dir = tmp_path / "bbw-config"
    monkeypatch.setenv("BBW_CONFIG_DIR", str(cfg_dir))
    return cfg_dir


@pytest.fixture()
def default_branch_name() -> str:
    return "main"


def _init_origin(origin_dir: Path, default_branch: str) -> None:
    origin_dir.mkdir(parents=True)
    _run(["init", "--initial-branch", default_branch], origin_dir)
    _run(["config", "user.email", "test@example.com"], origin_dir)
    _run(["config", "user.name", "Test"], origin_dir)
    (origin_dir / "file.txt").write_text("hello\n")
    # shared.txt exists from the initial commit so both origin and every clone
    # start out tracking it -- needed by fixture_repos_conflict_overlap and
    # fixture_repos_behind_no_overlap (Phase 2 conflict-risk fixtures) to give
    # both sides a common path to independently edit.
    (origin_dir / "shared.txt").write_text("shared original\n")
    _run(["add", "file.txt", "shared.txt"], origin_dir)
    _run(["commit", "-m", "initial commit"], origin_dir)


@pytest.fixture()
def fixture_repos(tmp_path, default_branch_name):
    """Build an origin repo + a clone one commit behind origin's default branch.

    Returns (origin_path, clone_path).
    """
    origin_dir = tmp_path / "origin"
    _init_origin(origin_dir, default_branch_name)

    clone_dir = tmp_path / "clone"
    _run(["clone", "--quiet", str(origin_dir), str(clone_dir)], tmp_path)
    _run(["config", "user.email", "test@example.com"], clone_dir)
    _run(["config", "user.name", "Test"], clone_dir)

    # Advance origin by one more commit so the clone is behind.
    (origin_dir / "file.txt").write_text("hello again\n")
    _run(["add", "file.txt"], origin_dir)
    _run(["commit", "-m", "second commit"], origin_dir)

    return str(origin_dir), str(clone_dir)


@pytest.fixture()
def fixture_repos_up_to_date(tmp_path, default_branch_name):
    """Build an origin repo + a clone level with origin's default branch."""
    origin_dir = tmp_path / "origin"
    _init_origin(origin_dir, default_branch_name)

    clone_dir = tmp_path / "clone"
    _run(["clone", "--quiet", str(origin_dir), str(clone_dir)], tmp_path)
    _run(["config", "user.email", "test@example.com"], clone_dir)
    _run(["config", "user.name", "Test"], clone_dir)

    return str(origin_dir), str(clone_dir)


def _clone_from(origin_dir: Path, clone_dir: Path, tmp_path: Path) -> None:
    _run(["clone", "--quiet", str(origin_dir), str(clone_dir)], tmp_path)
    _run(["config", "user.email", "test@example.com"], clone_dir)
    _run(["config", "user.name", "Test"], clone_dir)


@pytest.fixture()
def fixture_repos_diverged(tmp_path, default_branch_name):
    """Clone with a local unique commit AND origin independently advanced -> diverged.

    Both sides add a commit on top of the same shared ancestor touching
    DIFFERENT paths (clone_only.txt vs file.txt), so the clone's HEAD is both
    behind (origin has a commit it lacks) and ahead (it has a commit origin
    lacks) of origin/<base>, with zero file-path overlap -- this fixture
    tests pure DIVERGED status calculation, distinct from the conflict-risk
    overlap fixtures (fixture_repos_conflict_overlap /
    fixture_repos_behind_no_overlap) which deliberately touch the same path.
    """
    origin_dir = tmp_path / "origin"
    _init_origin(origin_dir, default_branch_name)

    clone_dir = tmp_path / "clone"
    _clone_from(origin_dir, clone_dir, tmp_path)

    (clone_dir / "clone_only.txt").write_text("local diverged change\n")
    _run(["add", "clone_only.txt"], clone_dir)
    _run(["commit", "-m", "local diverged commit"], clone_dir)

    (origin_dir / "file.txt").write_text("origin advanced change\n")
    _run(["add", "file.txt"], origin_dir)
    _run(["commit", "-m", "origin advanced commit"], origin_dir)

    return str(origin_dir), str(clone_dir)


@pytest.fixture()
def fixture_repos_unpushed(tmp_path, default_branch_name):
    """Clone level with origin/base but with a commit never pushed to its own upstream.

    The local commit is ahead of origin/<base> (which never advances), so the
    per-base comparison stays UP_TO_DATE (behind==0, ahead alone is not flagged);
    unpushed_count (measured against the same, un-advanced @{u}) is nonzero.
    """
    origin_dir = tmp_path / "origin"
    _init_origin(origin_dir, default_branch_name)

    clone_dir = tmp_path / "clone"
    _clone_from(origin_dir, clone_dir, tmp_path)

    (clone_dir / "file.txt").write_text("local unpushed change\n")
    _run(["add", "file.txt"], clone_dir)
    _run(["commit", "-m", "local unpushed commit"], clone_dir)

    return str(origin_dir), str(clone_dir)


@pytest.fixture()
def fixture_repos_behind_and_unpushed(tmp_path, default_branch_name):
    """Clone behind base (pure ancestor, not diverged) AND unpushed to its own upstream.

    Construction:
    - The clone commits locally (commit B) on top of origin's initial commit (A),
      and does NOT push it via the normal tracked branch.
    - The clone's own-upstream tracking is repointed to a frozen snapshot ref
      (origin/<base>-snapshot) taken before origin advances further, so a later
      `git fetch origin <base>` (which only updates refs/remotes/origin/<base>)
      never touches it -- unpushed_count(@{u}..HEAD) stays nonzero.
    - Commit B's object is transferred into origin under a side ref and folded
      into origin/<base> via a fast-forward merge, then origin adds one more
      commit (C) on top. Since B is a strict ancestor of C, the clone's HEAD (B)
      is a pure ancestor of origin/<base> (C) after fetch: behind>0, ahead==0 --
      BEHIND, not DIVERGED.
    """
    origin_dir = tmp_path / "origin"
    _init_origin(origin_dir, default_branch_name)

    clone_dir = tmp_path / "clone"
    _clone_from(origin_dir, clone_dir, tmp_path)

    # Freeze a snapshot of origin/<base>'s current position and repoint the
    # local branch's own upstream at it (instead of the live remote-tracking
    # ref), so subsequent fetches of <base> never advance what unpushed_count sees.
    snapshot_ref = f"refs/remotes/origin/{default_branch_name}-snapshot"
    _run(["update-ref", snapshot_ref, f"refs/remotes/origin/{default_branch_name}"], clone_dir)
    _run(
        [
            "branch",
            "--set-upstream-to",
            f"origin/{default_branch_name}-snapshot",
            default_branch_name,
        ],
        clone_dir,
    )

    # Local unpushed commit B on top of the frozen snapshot position.
    (clone_dir / "file.txt").write_text("local unpushed change\n")
    _run(["add", "file.txt"], clone_dir)
    _run(["commit", "-m", "local unpushed commit"], clone_dir)

    # Transfer commit B's object into origin under a side ref (origin's
    # currently-checked-out branch is <base>, so pushing to a *different*
    # branch name is allowed on a non-bare repo).
    _run(["push", "--quiet", "origin", "HEAD:refs/heads/_staging"], clone_dir)

    # Fold B into origin/<base> (fast-forward, B's parent is origin's tip) and
    # advance <base> with one more commit (C), so B ends up a strict ancestor.
    _run(["merge", "--ff-only", "_staging"], origin_dir)
    (origin_dir / "extra.txt").write_text("origin advanced further\n")
    _run(["add", "extra.txt"], origin_dir)
    _run(["commit", "-m", "origin advanced commit"], origin_dir)

    return str(origin_dir), str(clone_dir)


@pytest.fixture()
def fixture_repos_multi_base(tmp_path, default_branch_name):
    """Repo with two base branches: default branch diverged, "release" up to date.

    "release" is branched from the same initial commit and never advances, so
    the clone's one local commit only makes it ahead of "release" (not flagged
    without a corresponding behind). The default branch independently advances
    on origin AND the clone has its own local commit -> diverged. The two
    sides touch DIFFERENT paths (clone_only.txt vs file.txt) so this fixture
    exercises pure DIVERGED status, not conflict-risk overlap.
    """
    origin_dir = tmp_path / "origin"
    _init_origin(origin_dir, default_branch_name)
    _run(["branch", "release"], origin_dir)

    clone_dir = tmp_path / "clone"
    _clone_from(origin_dir, clone_dir, tmp_path)

    (clone_dir / "clone_only.txt").write_text("clone diverged change\n")
    _run(["add", "clone_only.txt"], clone_dir)
    _run(["commit", "-m", "clone diverged commit"], clone_dir)

    (origin_dir / "file.txt").write_text("origin advanced change\n")
    _run(["add", "file.txt"], origin_dir)
    _run(["commit", "-m", "origin advanced commit"], origin_dir)

    return str(origin_dir), str(clone_dir)


@pytest.fixture()
def fixture_repo_no_upstream(tmp_path, default_branch_name):
    """A plain repo with commits but no remote/upstream configured at all."""
    repo_dir = tmp_path / "solo"
    _init_origin(repo_dir, default_branch_name)
    return str(repo_dir)


@pytest.fixture()
def fixture_repos_conflict_overlap(tmp_path, default_branch_name):
    """Clone has a branch-unique commit touching shared.txt AND an uncommitted
    working-tree edit to file.txt; origin independently advances the SAME
    shared.txt path -> the incoming range and the local change set overlap.

    Exercises both halves of D-01's local scope: the branch-unique commit
    (shared.txt) and the working-tree diff (file.txt), though only shared.txt
    is expected to actually overlap with origin's incoming change.
    """
    origin_dir = tmp_path / "origin"
    _init_origin(origin_dir, default_branch_name)

    clone_dir = tmp_path / "clone"
    _clone_from(origin_dir, clone_dir, tmp_path)

    (clone_dir / "shared.txt").write_text("clone edited shared\n")
    _run(["add", "shared.txt"], clone_dir)
    _run(["commit", "-m", "clone edits shared.txt"], clone_dir)

    # Uncommitted working-tree edit to a different tracked file -- exercises
    # D-01's working-tree half without contributing to the overlap itself.
    (clone_dir / "file.txt").write_text("clone working-tree edit\n")

    (origin_dir / "shared.txt").write_text("origin advanced shared\n")
    _run(["add", "shared.txt"], origin_dir)
    _run(["commit", "-m", "origin advances shared.txt"], origin_dir)

    return str(origin_dir), str(clone_dir)


@pytest.fixture()
def fixture_repos_behind_no_overlap(tmp_path, default_branch_name):
    """Clone is behind on shared.txt (origin advances it) but the clone's only
    local change touches a distinct file (other.txt) -> zero path overlap.
    """
    origin_dir = tmp_path / "origin"
    _init_origin(origin_dir, default_branch_name)

    clone_dir = tmp_path / "clone"
    _clone_from(origin_dir, clone_dir, tmp_path)

    (clone_dir / "other.txt").write_text("clone local change\n")
    _run(["add", "other.txt"], clone_dir)
    _run(["commit", "-m", "clone edits other.txt"], clone_dir)

    (origin_dir / "shared.txt").write_text("origin advanced shared\n")
    _run(["add", "shared.txt"], origin_dir)
    _run(["commit", "-m", "origin advances shared.txt"], origin_dir)

    return str(origin_dir), str(clone_dir)


@pytest.fixture()
def fixture_repos_incoming_rename(tmp_path, default_branch_name):
    """Origin renames file.txt -> renamed.txt while the clone has a
    branch-unique commit editing the OLD file.txt -- regression fixture for
    02-RESEARCH.md Pitfall 1: a naive `--name-only` parse drops the old path
    of a rename and would silently miss this conflict shape; `--name-status
    -z` (adding both R-record paths, see `_parse_name_status_z`) closes it.
    """
    origin_dir = tmp_path / "origin"
    _init_origin(origin_dir, default_branch_name)

    clone_dir = tmp_path / "clone"
    _clone_from(origin_dir, clone_dir, tmp_path)

    (clone_dir / "file.txt").write_text("clone edits file.txt under its old name\n")
    _run(["add", "file.txt"], clone_dir)
    _run(["commit", "-m", "clone edits file.txt under its old name"], clone_dir)

    _run(["mv", "file.txt", "renamed.txt"], origin_dir)
    _run(["commit", "-m", "origin renames file.txt to renamed.txt"], origin_dir)

    return str(origin_dir), str(clone_dir)


@pytest.fixture()
def fixture_repos_no_common_ancestor(tmp_path, default_branch_name):
    """Origin's base branch is rebuilt as an orphan (unrelated) history via
    `git checkout --orphan` + `git branch -M`, sharing no common ancestor
    with the clone's HEAD, while `behind_ahead` still reports the clone as
    behind -- regression fixture for 02-RESEARCH.md Pitfall 3/5: `merge_base`
    must return None here (exit 1, no common ancestor) so `check_repo` routes
    to CHECK_FAILED, never a silent no-conflict / bogus BEHIND result.
    """
    origin_dir = tmp_path / "origin"
    _init_origin(origin_dir, default_branch_name)

    clone_dir = tmp_path / "clone"
    _clone_from(origin_dir, clone_dir, tmp_path)

    _run(["checkout", "--orphan", "rewritten-base"], origin_dir)
    _run(["rm", "-rf", "."], origin_dir)
    (origin_dir / "unrelated.txt").write_text("no shared history\n")
    _run(["add", "unrelated.txt"], origin_dir)
    _run(["commit", "-m", "rewritten unrelated history"], origin_dir)
    _run(["branch", "-M", "rewritten-base", default_branch_name], origin_dir)

    return str(origin_dir), str(clone_dir)


@pytest.fixture()
def fixture_repos_fetch_fails(tmp_path, default_branch_name):
    """Clone whose origin remote points at a path that no longer exists."""
    origin_dir = tmp_path / "origin"
    _init_origin(origin_dir, default_branch_name)

    clone_dir = tmp_path / "clone"
    _clone_from(origin_dir, clone_dir, tmp_path)

    missing_remote = tmp_path / "does-not-exist-remote"
    _run(["remote", "set-url", "origin", str(missing_remote)], clone_dir)

    return str(origin_dir), str(clone_dir)

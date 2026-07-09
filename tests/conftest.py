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
    _run(["add", "file.txt"], origin_dir)
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

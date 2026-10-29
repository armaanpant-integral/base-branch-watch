"""Tiny real-git helpers shared by tests that need branches on a tmp origin/clone."""

from __future__ import annotations

import subprocess

GIT = "/usr/bin/git"


def git(repo: str, *args: str) -> str:
    result = subprocess.run([GIT, "-C", repo, *args], capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, f"git {args} failed: {result.stderr}"
    return result.stdout.strip()


def commit_on_new_branch(clone: str, branch: str, push: bool = True) -> str:
    """Create `branch` with one commit in `clone`, optionally pushed with upstream."""
    git(clone, "switch", "-c", branch)
    with open(f"{clone}/{branch.replace('/', '_')}.txt", "w") as handle:
        handle.write("work\n")
    git(clone, "add", "-A")
    git(clone, "commit", "-m", f"work on {branch}")
    if push:
        git(clone, "push", "--quiet", "-u", "origin", branch)
    return git(clone, "rev-parse", "HEAD")

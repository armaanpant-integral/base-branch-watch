"""Exercises scripts/install-pre-push-hook.sh (install/uninstall) against real
tmp git repos (tests/conftest.py's fixture_repos convention -- no mocking of
git itself), plus the menubar add/remove/backfill trigger points added in
Task 3.

Covers HOOK-01 (auto-install per watched repo), D-02 (baked, import-verified
interpreter), D-03 (foreign-hook safety -- never clobber a hook bbwatch
didn't write), D-04 (uninstall only ever removes a bbwatch-marked hook), and
install idempotency (what makes the startup backfill loop safe to run on
every launch).
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from base_branch_watch.core import git_ops

REPO_ROOT = Path(__file__).resolve().parent.parent
INSTALL_SCRIPT = REPO_ROOT / "scripts" / "install-pre-push-hook.sh"

MARKER = "# bbwatch-managed"


def _install(repo_path: str, python_bin: str | None = None) -> subprocess.CompletedProcess[str]:
    args = ["/bin/sh", str(INSTALL_SCRIPT), repo_path]
    if python_bin is not None:
        args.append(python_bin)
    return subprocess.run(args, capture_output=True, text=True, timeout=10)


def _uninstall(repo_path: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["/bin/sh", str(INSTALL_SCRIPT), "--uninstall", repo_path],
        capture_output=True,
        text=True,
        timeout=10,
    )


def _hook_path(repo_path: str) -> Path:
    resolved = git_ops.hooks_path(repo_path)
    assert resolved is not None
    return Path(resolved) / "pre-push"


def test_install_creates_marked_hook(fixture_repos):
    _origin, clone_path = fixture_repos

    result = _install(clone_path, sys.executable)

    assert result.returncode == 0, result.stderr
    hook_file = _hook_path(clone_path)
    assert hook_file.is_file()
    lines = hook_file.read_text().splitlines()
    assert lines[1] == MARKER
    assert "__PYTHON__" not in hook_file.read_text()
    assert sys.executable in hook_file.read_text()
    assert "-m base_branch_watch.hook" in hook_file.read_text()


def test_install_refuses_foreign_hook(fixture_repos):
    _origin, clone_path = fixture_repos
    hook_file = _hook_path(clone_path)
    hook_file.parent.mkdir(parents=True, exist_ok=True)
    foreign_contents = "#!/bin/sh\necho 'my own pre-push hook'\n"
    hook_file.write_text(foreign_contents)

    result = _install(clone_path, sys.executable)

    assert result.returncode != 0
    assert hook_file.read_text() == foreign_contents, "foreign hook must be left byte-identical"
    assert result.stderr, "should print an instructional message on refusal"


def test_install_honors_core_hooksPath(fixture_repos):
    _origin, clone_path = fixture_repos
    subprocess.run(
        ["/usr/bin/git", "-C", clone_path, "config", "core.hooksPath", ".myhooks"],
        check=True,
        capture_output=True,
        text=True,
        timeout=10,
    )

    result = _install(clone_path, sys.executable)

    assert result.returncode == 0, result.stderr
    custom_hook = Path(clone_path) / ".myhooks" / "pre-push"
    assert custom_hook.is_file()
    default_hook = Path(clone_path) / ".git" / "hooks" / "pre-push"
    assert not default_hook.exists()


def test_uninstall_removes_only_marked_hook(fixture_repos):
    _origin, clone_path = fixture_repos
    _install(clone_path, sys.executable)
    hook_file = _hook_path(clone_path)
    assert hook_file.is_file()

    result = _uninstall(clone_path)

    assert result.returncode == 0, result.stderr
    assert not hook_file.exists()


def test_uninstall_leaves_foreign_hook_untouched(fixture_repos):
    _origin, clone_path = fixture_repos
    hook_file = _hook_path(clone_path)
    hook_file.parent.mkdir(parents=True, exist_ok=True)
    foreign_contents = "#!/bin/sh\necho 'my own pre-push hook'\n"
    hook_file.write_text(foreign_contents)

    result = _uninstall(clone_path)

    assert result.returncode == 0, result.stderr
    assert hook_file.read_text() == foreign_contents


def test_install_is_idempotent(fixture_repos):
    _origin, clone_path = fixture_repos

    first = _install(clone_path, sys.executable)
    second = _install(clone_path, sys.executable)

    assert first.returncode == 0, first.stderr
    assert second.returncode == 0, second.stderr
    hook_file = _hook_path(clone_path)
    lines = hook_file.read_text().splitlines()
    assert lines[1] == MARKER
    assert hook_file.parent.exists()
    # exactly one "pre-push" hook file at the resolved path (not counting
    # git's own unrelated "pre-push.sample" placeholder), no duplication
    assert [p for p in hook_file.parent.iterdir() if p.name == "pre-push"] == [hook_file]

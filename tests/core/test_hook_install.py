"""core.hook_install against real tmp git repos (tests/conftest.py's fixture_repos).

Covers HOOK-01 (install per watched repo), D-02 (baked, import-verified interpreter),
D-03 (foreign-hook safety - never clobber a hook bbwatch didn't write), D-04 (uninstall
only removes a bbwatch-marked hook) and idempotency (what makes the menubar's
startup backfill safe on every launch).
"""

from __future__ import annotations

import os
import stat
import subprocess
import sys
from pathlib import Path

from base_branch_watch.core import git_ops, hook_install

MARKER = "# bbwatch-managed"
FOREIGN = "#!/bin/sh\necho 'my own pre-push hook'\n"


def _hook_path(repo_path: str) -> Path:
    resolved = git_ops.hooks_path(repo_path)
    assert resolved is not None
    return Path(resolved) / "pre-push"


def _write_foreign_hook(repo_path: str) -> Path:
    hook_file = _hook_path(repo_path)
    hook_file.parent.mkdir(parents=True, exist_ok=True)
    hook_file.write_text(FOREIGN)
    return hook_file


def test_install_creates_marked_executable_hook(fixture_repos):
    _origin, clone_path = fixture_repos

    result = hook_install.install(clone_path, sys.executable)

    assert result.ok, result.message
    hook_file = _hook_path(clone_path)
    text = hook_file.read_text()
    assert text.splitlines()[1] == MARKER
    assert "__PYTHON__" not in text
    assert sys.executable in text
    assert "-m base_branch_watch.hook" in text
    assert os.access(hook_file, os.X_OK)
    assert result.message == f"Installed bbwatch pre-push hook: {hook_file}"


def test_install_refuses_foreign_hook_and_leaves_it_byte_identical(fixture_repos):
    _origin, clone_path = fixture_repos
    hook_file = _write_foreign_hook(clone_path)

    result = hook_install.install(clone_path, sys.executable)

    assert not result.ok
    assert hook_file.read_text() == FOREIGN
    assert "not bbwatch-managed" in result.message
    assert "base_branch_watch.hook" in result.message, "should explain how to chain manually"


def test_install_honors_core_hooksPath(fixture_repos):
    _origin, clone_path = fixture_repos
    subprocess.run(
        ["/usr/bin/git", "-C", clone_path, "config", "core.hooksPath", ".myhooks"],
        check=True,
        capture_output=True,
        text=True,
        timeout=10,
    )

    result = hook_install.install(clone_path, sys.executable)

    assert result.ok, result.message
    assert (Path(clone_path) / ".myhooks" / "pre-push").is_file()
    assert not (Path(clone_path) / ".git" / "hooks" / "pre-push").exists()


def test_install_is_idempotent(fixture_repos):
    _origin, clone_path = fixture_repos

    first = hook_install.install(clone_path, sys.executable)
    second = hook_install.install(clone_path, sys.executable)

    assert first.ok and second.ok
    hook_file = _hook_path(clone_path)
    assert hook_file.read_text().splitlines()[1] == MARKER
    assert [p for p in hook_file.parent.iterdir() if p.name == "pre-push"] == [hook_file]


def test_install_defaults_to_python3_on_path(fixture_repos, tmp_path, monkeypatch):
    _origin, clone_path = fixture_repos
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    link = fake_bin / "python3"
    link.symlink_to(sys.executable)
    monkeypatch.setenv("PATH", str(fake_bin))

    result = hook_install.install(clone_path)

    assert result.ok, result.message
    assert str(link) in _hook_path(clone_path).read_text()


def test_install_resolves_a_relative_interpreter_name_to_an_absolute_path(
    fixture_repos, tmp_path, monkeypatch
):
    _origin, clone_path = fixture_repos
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    (fake_bin / "mypython").symlink_to(sys.executable)
    monkeypatch.setenv("PATH", str(fake_bin))

    result = hook_install.install(clone_path, "mypython")

    assert result.ok, result.message
    assert f'exec "{fake_bin / "mypython"}"' in _hook_path(clone_path).read_text()


def test_install_fails_when_no_interpreter_is_found(fixture_repos, tmp_path, monkeypatch):
    _origin, clone_path = fixture_repos
    monkeypatch.setenv("PATH", str(tmp_path))

    result = hook_install.install(clone_path)

    assert not result.ok
    assert "python3" in result.message
    assert not _hook_path(clone_path).exists()


def test_install_fails_when_a_relative_interpreter_is_not_on_path(
    fixture_repos, tmp_path, monkeypatch
):
    _origin, clone_path = fixture_repos
    monkeypatch.setenv("PATH", str(tmp_path))

    result = hook_install.install(clone_path, "no-such-python")

    assert not result.ok
    assert "no-such-python" in result.message


def test_install_fails_when_the_interpreter_cannot_import_the_package(fixture_repos, tmp_path):
    _origin, clone_path = fixture_repos
    bare_python = tmp_path / "bare-python"
    bare_python.write_text("#!/bin/sh\nexit 1\n")
    bare_python.chmod(bare_python.stat().st_mode | stat.S_IEXEC)

    result = hook_install.install(clone_path, str(bare_python))

    assert not result.ok
    assert "cannot import base_branch_watch" in result.message
    assert not _hook_path(clone_path).exists()


def test_install_fails_for_a_path_that_is_not_a_git_repository(tmp_path):
    result = hook_install.install(str(tmp_path), sys.executable)

    assert not result.ok
    assert "does not look like a git repository" in result.message


def test_install_survives_an_interpreter_path_with_sed_metacharacters(fixture_repos, tmp_path):
    """WR-03 regression: the old sed-based templating needed escaping for & # \\."""
    _origin, clone_path = fixture_repos
    odd_dir = tmp_path / "a&b#c"
    odd_dir.mkdir()
    odd_python = odd_dir / "python3"
    odd_python.symlink_to(sys.executable)

    result = hook_install.install(clone_path, str(odd_python))

    assert result.ok, result.message
    assert f'exec "{odd_python}"' in _hook_path(clone_path).read_text()


def test_uninstall_removes_only_a_marked_hook(fixture_repos):
    _origin, clone_path = fixture_repos
    hook_install.install(clone_path, sys.executable)
    hook_file = _hook_path(clone_path)

    result = hook_install.uninstall(clone_path)

    assert result.ok, result.message
    assert not hook_file.exists()
    assert result.message == f"Removed bbwatch-managed hook: {hook_file}"


def test_uninstall_leaves_a_foreign_hook_untouched(fixture_repos):
    _origin, clone_path = fixture_repos
    hook_file = _write_foreign_hook(clone_path)

    result = hook_install.uninstall(clone_path)

    assert result.ok, result.message
    assert hook_file.read_text() == FOREIGN
    assert "not a bbwatch-managed hook" in result.message


def test_uninstall_with_no_hook_is_a_successful_no_op(fixture_repos):
    _origin, clone_path = fixture_repos

    result = hook_install.uninstall(clone_path)

    assert result.ok
    assert "nothing to remove" in result.message


def test_uninstall_fails_for_a_path_that_is_not_a_git_repository(tmp_path):
    result = hook_install.uninstall(str(tmp_path))

    assert not result.ok
    assert "does not look like a git repository" in result.message


def test_template_ships_as_package_data():
    from importlib import resources

    template = resources.files("base_branch_watch.core").joinpath("pre-push-hook.sh.template")

    assert "__PYTHON__" in template.read_text()

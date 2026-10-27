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


def _python_wrapper(path: Path) -> Path:
    """A distinct interpreter path that still runs the test venv's python (symlinks would lose
    the venv)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f'#!/bin/sh\nexec "{sys.executable}" "$@"\n')
    path.chmod(0o755)
    return path


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
    link = _python_wrapper(fake_bin / "python3")
    monkeypatch.setenv("PATH", str(fake_bin))

    result = hook_install.install(clone_path)

    assert result.ok, result.message
    assert str(link) in _hook_path(clone_path).read_text()


def test_install_resolves_a_relative_interpreter_name_to_an_absolute_path(
    fixture_repos, tmp_path, monkeypatch
):
    _origin, clone_path = fixture_repos
    fake_bin = tmp_path / "bin"
    _python_wrapper(fake_bin / "mypython")
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
    odd_python = _python_wrapper(tmp_path / "a&b#c" / "python3")

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


def test_install_makes_a_relative_interpreter_path_absolute(fixture_repos, tmp_path, monkeypatch):
    _origin, clone_path = fixture_repos
    _python_wrapper(tmp_path / "venv-bin" / "python")
    monkeypatch.chdir(tmp_path)

    result = hook_install.install(clone_path, "./venv-bin/python")

    assert result.ok, result.message
    assert f'exec "{tmp_path / "venv-bin" / "python"}"' in _hook_path(clone_path).read_text()


def test_install_message_reports_an_absolute_hook_path_for_a_relative_repo_path(
    fixture_repos, monkeypatch
):
    _origin, clone_path = fixture_repos
    monkeypatch.chdir(clone_path)

    result = hook_install.install(".", sys.executable)

    assert result.ok, result.message
    assert result.message == f"Installed bbwatch pre-push hook: {_hook_path(clone_path)}"


def test_install_and_uninstall_never_raise_when_the_hooks_dir_is_unreadable(fixture_repos):
    _origin, clone_path = fixture_repos
    hooks_dir = _hook_path(clone_path).parent
    hooks_dir.mkdir(parents=True, exist_ok=True)
    hooks_dir.chmod(0o000)
    try:
        installed = hook_install.install(clone_path, sys.executable)
        removed = hook_install.uninstall(clone_path)
    finally:
        hooks_dir.chmod(0o755)

    assert not installed.ok
    assert removed.ok


def test_import_check_does_not_trust_the_callers_working_directory(fixture_repos, tmp_path):
    """`python -c` puts cwd on sys.path; an interpreter that only imports the package
    because cwd is a checkout must not pass."""
    _origin, clone_path = fixture_repos
    cwd_only_python = tmp_path / "cwd-only-python"
    cwd_only_python.write_text('#!/bin/sh\n[ -f "$PWD/base_branch_watch/__init__.py" ]\n')
    cwd_only_python.chmod(0o755)

    result = hook_install.install(clone_path, str(cwd_only_python))

    assert not result.ok
    assert "cannot import base_branch_watch" in result.message

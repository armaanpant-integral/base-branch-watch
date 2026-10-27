"""Install / uninstall the bbwatch pre-push conflict-gate hook. No rumps/AppKit import, ever.

One module behind `bbwatch install-hook` / `uninstall-hook` and the menu-bar app's
add / remove / startup-backfill triggers. Both functions return a `HookResult` and never
raise, so one bad repo can't abort a backfill loop or the add-repo flow.

- The hooks directory always comes from `git_ops.hooks_path` (honors core.hooksPath,
  03-RESEARCH.md Pitfall 1), never a hardcoded `.git/hooks`.
- A pre-existing hook that is not bbwatch-managed is never overwritten or removed (D-03/D-04).
- Re-installing over a bbwatch-managed hook rewrites it, which is what makes the startup
  backfill safe to run on every launch.
- The interpreter is resolved to an absolute path at install time and must be able to
  `import base_branch_watch` (D-02).
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from importlib import resources
from pathlib import Path

from base_branch_watch.core import git_ops

MARKER = "# bbwatch-managed"
_TEMPLATE_NAME = "pre-push-hook.sh.template"
_PYTHON_PLACEHOLDER = "__PYTHON__"
_IMPORT_CHECK_TIMEOUT_SECONDS = 15


@dataclass(frozen=True)
class HookResult:
    ok: bool
    message: str


def install(repo_path: str, python_bin: str | None = None) -> HookResult:
    """Install (or re-install) the pre-push hook into `repo_path`. Never raises."""
    resolved = _resolve_interpreter(python_bin)
    if isinstance(resolved, HookResult):
        return resolved
    import_error = _check_importable(resolved)
    if import_error is not None:
        return import_error

    hook_path = _hook_path(repo_path)
    if isinstance(hook_path, HookResult):
        return hook_path
    if _is_file(hook_path) and not _is_bbwatch_hook(hook_path):
        return HookResult(
            ok=False,
            message=(
                f"{hook_path} already exists and is not bbwatch-managed. "
                "Refusing to overwrite a hook bbwatch did not install. "
                "To use bbwatch's pre-push gate alongside your existing hook, add a line "
                f'to it that execs/chains into: "{resolved}" -m base_branch_watch.hook "$@"'
            ),
        )

    try:
        template = resources.files("base_branch_watch.core").joinpath(_TEMPLATE_NAME).read_text()
        hook_path.parent.mkdir(parents=True, exist_ok=True)
        hook_path.write_text(template.replace(_PYTHON_PLACEHOLDER, resolved))
        hook_path.chmod(0o755)
    except OSError as exc:
        return HookResult(ok=False, message=f"could not write {hook_path}: {exc}")
    return HookResult(ok=True, message=f"Installed bbwatch pre-push hook: {hook_path}")


def uninstall(repo_path: str) -> HookResult:
    """Remove the pre-push hook from `repo_path`, only if bbwatch wrote it. Never raises."""
    hook_path = _hook_path(repo_path)
    if isinstance(hook_path, HookResult):
        return hook_path
    if not _is_file(hook_path):
        return HookResult(
            ok=True, message=f"No pre-push hook found at {hook_path} (nothing to remove)."
        )
    if not _is_bbwatch_hook(hook_path):
        return HookResult(
            ok=True,
            message=f"Leaving {hook_path} untouched - it is not a bbwatch-managed hook.",
        )
    try:
        hook_path.unlink()
    except OSError as exc:
        return HookResult(ok=False, message=f"could not remove {hook_path}: {exc}")
    return HookResult(ok=True, message=f"Removed bbwatch-managed hook: {hook_path}")


def _hook_path(repo_path: str) -> Path | HookResult:
    hooks_dir = git_ops.hooks_path(repo_path)
    if hooks_dir is None:
        return HookResult(
            ok=False,
            message=f"'{repo_path}' does not look like a git repository (rev-parse failed).",
        )
    return Path(os.path.abspath(hooks_dir)) / "pre-push"


def _is_file(path: Path) -> bool:
    """Path.is_file() raises PermissionError on an unreadable parent; `[ -f ]` did not."""
    try:
        return path.is_file()
    except OSError:
        return False


def _is_bbwatch_hook(hook_path: Path) -> bool:
    try:
        first_lines = hook_path.read_text(errors="replace").splitlines()[:2]
    except OSError:
        return False
    return MARKER in first_lines


def _resolve_interpreter(python_bin: str | None) -> str | HookResult:
    name = python_bin or "python3"
    if name.startswith("/"):
        return name
    found = shutil.which(name)
    if found is None:
        if python_bin is None:
            return HookResult(
                ok=False,
                message="no python3 interpreter found on PATH and none given as an argument.",
            )
        return HookResult(
            ok=False, message=f"'{name}' is not an absolute path and was not found on PATH."
        )
    return os.path.abspath(found)


def _check_importable(python_bin: str) -> HookResult | None:
    try:
        result = subprocess.run(
            [python_bin, "-c", "import base_branch_watch"],
            capture_output=True,
            timeout=_IMPORT_CHECK_TIMEOUT_SECONDS,
            # Neutral cwd: `python -c` puts cwd on sys.path, which would let a checkout
            # directory mask an interpreter that cannot import the package on its own.
            cwd=tempfile.gettempdir(),
        )
    except (subprocess.TimeoutExpired, OSError):
        return _cannot_import(python_bin)
    return None if result.returncode == 0 else _cannot_import(python_bin)


def _cannot_import(python_bin: str) -> HookResult:
    return HookResult(
        ok=False,
        message=(
            f"{python_bin} cannot import base_branch_watch. Install the package into this "
            "interpreter first (e.g. 'pip install -e .' from this repo with that "
            "interpreter's venv active), or pass the correct interpreter path."
        ),
    )

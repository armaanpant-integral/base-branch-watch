"""e2e + unit coverage of the `python3 -m base_branch_watch.hook` entry point.

No mocking of git itself -- exercises real tmp git repos per the project-wide
convention (see tests/conftest.py). The only thing monkeypatched is
`builtins.open` (to simulate a no-controlling-terminal /dev/tty per
RESEARCH.md Pattern 3) and `sys.stdin` (git repurposes stdin for pre-push
ref-update data, so tests feed it the exact captured protocol strings).
"""

from __future__ import annotations

import builtins
import io
import json
import subprocess
from pathlib import Path

from base_branch_watch import hook
from base_branch_watch.core import base_verdict, git_ops
from base_branch_watch.core import config as config_module
from base_branch_watch.core.models import RepoConfig, StatusKind
from tests.conftest import _run, raise_timeout

ZERO_SHA = "0" * 40


def _configure_repo(clone_path: str, base_branch: str) -> None:
    cfg = config_module.AppConfig(
        repos=[RepoConfig(repo_path=clone_path, base_branches=[base_branch])]
    )
    config_module.save_config(cfg)


def _pre_push_line(branch: str, local_sha: str) -> str:
    return f"refs/heads/{branch} {local_sha} refs/heads/{branch} {ZERO_SHA}\n"


def _patch_no_tty(monkeypatch) -> dict:
    """Simulate `open('/dev/tty')` raising OSError (no controlling terminal),
    while passing every other path through to the real `open`. Returns a
    dict tracking whether /dev/tty was ever attempted."""
    tty_opened = {"called": False}
    real_open = builtins.open

    def fake_open(path, *args, **kwargs):
        if path == "/dev/tty":
            tty_opened["called"] = True
            raise OSError(6, "Device not configured")
        return real_open(path, *args, **kwargs)

    monkeypatch.setattr("builtins.open", fake_open)
    return tty_opened


def test_hook_gate_warns_and_allows_on_conflict_without_tty(
    fixture_repos_conflict_overlap, default_branch_name, bbw_config_dir, monkeypatch, capsys
):
    _origin, clone_path = fixture_repos_conflict_overlap
    _configure_repo(clone_path, default_branch_name)
    local_sha = git_ops.resolve_ref(clone_path, "HEAD")
    assert local_sha is not None

    monkeypatch.setattr("sys.stdin", io.StringIO(_pre_push_line(default_branch_name, local_sha)))
    monkeypatch.chdir(clone_path)
    _patch_no_tty(monkeypatch)

    exit_code = hook.main(["origin", "git@example.com:test.git"])

    assert exit_code == 0
    captured = capsys.readouterr()
    assert default_branch_name in captured.out
    assert "⚠️" in captured.out
    assert "Potential conflicts detected" in captured.err
    assert "No terminal available" in captured.err


def test_hook_clean_repo_exits_zero_no_prompt(
    fixture_repos, default_branch_name, bbw_config_dir, monkeypatch, capsys
):
    _origin, clone_path = fixture_repos
    _configure_repo(clone_path, default_branch_name)
    local_sha = git_ops.resolve_ref(clone_path, "HEAD")
    assert local_sha is not None

    monkeypatch.setattr("sys.stdin", io.StringIO(_pre_push_line(default_branch_name, local_sha)))
    monkeypatch.chdir(clone_path)
    tty_opened = _patch_no_tty(monkeypatch)

    exit_code = hook.main(["origin", "git@example.com:test.git"])

    assert exit_code == 0
    assert tty_opened["called"] is False
    captured = capsys.readouterr()
    assert default_branch_name in captured.out
    assert "incoming commit" in captured.out


def test_hook_skip_gate_env_bypasses(
    fixture_repos_conflict_overlap, default_branch_name, bbw_config_dir, monkeypatch, capsys
):
    _origin, clone_path = fixture_repos_conflict_overlap
    _configure_repo(clone_path, default_branch_name)
    local_sha = git_ops.resolve_ref(clone_path, "HEAD")
    assert local_sha is not None

    monkeypatch.setattr("sys.stdin", io.StringIO(_pre_push_line(default_branch_name, local_sha)))
    monkeypatch.chdir(clone_path)
    monkeypatch.setenv("BBWATCH_SKIP_GATE", "1")
    tty_opened = _patch_no_tty(monkeypatch)

    exit_code = hook.main(["origin", "git@example.com:test.git"])

    assert exit_code == 0
    assert tty_opened["called"] is False


def test_hook_gates_when_fetch_fails_even_if_stale_ref_looks_clean(
    fixture_repos, default_branch_name, bbw_config_dir, monkeypatch, capsys
):
    _origin, clone_path = fixture_repos
    _configure_repo(clone_path, default_branch_name)
    local_sha = git_ops.resolve_ref(clone_path, "HEAD")
    assert local_sha is not None

    monkeypatch.setattr("sys.stdin", io.StringIO(_pre_push_line(default_branch_name, local_sha)))
    monkeypatch.chdir(clone_path)
    monkeypatch.setattr(
        git_ops,
        "fetch_with_retry",
        lambda repo_path, base: git_ops.FetchResult(ok=False, error="network down"),
    )
    prompts: list[str] = []

    def fake_prompt(message: str) -> bool:
        prompts.append(message)
        return False

    monkeypatch.setattr(hook, "prompt_push_anyway", fake_prompt)

    exit_code = hook.main(["origin", "git@example.com:test.git"])

    assert exit_code != 0
    assert len(prompts) == 1
    assert "Could not determine" in prompts[0]


def _hook_gates(clone_path, base, monkeypatch, capsys) -> bool:
    local_sha = git_ops.resolve_ref(clone_path, "HEAD")
    monkeypatch.setattr("sys.stdin", io.StringIO(_pre_push_line(base, local_sha)))
    monkeypatch.chdir(clone_path)
    _patch_no_tty(monkeypatch)
    hook.main(["origin", "git@example.com:test.git"])
    return "No terminal available" in capsys.readouterr().err


def test_menu_and_push_gate_agree_on_a_real_conflict(
    fixture_repos_conflict_overlap, default_branch_name, bbw_config_dir, monkeypatch, capsys
):
    _origin, clone_path = fixture_repos_conflict_overlap
    _configure_repo(clone_path, default_branch_name)

    menu = base_verdict.check_repo(
        RepoConfig(repo_path=clone_path, base_branches=[default_branch_name])
    )

    assert menu.branch_statuses[0].kind == StatusKind.CONFLICT_RISK
    assert _hook_gates(clone_path, default_branch_name, monkeypatch, capsys) is True


def test_menu_and_push_gate_agree_when_the_merge_is_clean(
    fixture_repos_incoming_rename, default_branch_name, bbw_config_dir, monkeypatch, capsys
):
    """Path overlap alone (edit vs rename) used to flag the menu while the gate,
    which asks merge-tree, stayed quiet. Both now answer from the same verdict."""
    _origin, clone_path = fixture_repos_incoming_rename
    _configure_repo(clone_path, default_branch_name)

    menu = base_verdict.check_repo(
        RepoConfig(repo_path=clone_path, base_branches=[default_branch_name])
    )

    assert menu.branch_statuses[0].kind != StatusKind.CONFLICT_RISK
    assert _hook_gates(clone_path, default_branch_name, monkeypatch, capsys) is False


def _run_hook(clone_path, base, monkeypatch, *, local_sha=None) -> int:
    local_sha = local_sha or git_ops.resolve_ref(clone_path, "HEAD")
    assert local_sha is not None
    monkeypatch.setattr("sys.stdin", io.StringIO(_pre_push_line(base, local_sha)))
    monkeypatch.chdir(clone_path)
    return hook.main(["origin", "git@example.com:test.git"])


def _capture_prompts(monkeypatch, answer: bool = False) -> list[str]:
    prompts: list[str] = []

    def fake_prompt(message: str) -> bool:
        prompts.append(message)
        return answer

    monkeypatch.setattr(hook, "prompt_push_anyway", fake_prompt)
    return prompts


def _configure(clone_path, bases, timeout=9) -> None:
    config_module.save_config(
        config_module.AppConfig(
            repos=[
                RepoConfig(
                    repo_path=clone_path, base_branches=bases, merge_tree_timeout_seconds=timeout
                )
            ]
        )
    )


def _make_release_diverged(origin_dir: str, default_branch_name: str) -> None:
    """fixture_repos_multi_base leaves `release` level with the clone's base; give it an
    incoming commit too so the clone is diverged from it and merge-tree runs."""
    origin = Path(origin_dir)
    _run(["checkout", "release"], origin)
    (origin / "release_only.txt").write_text("release advanced\n")
    _run(["add", "release_only.txt"], origin)
    _run(["commit", "-m", "release advanced commit"], origin)
    _run(["checkout", default_branch_name], origin)


def _per_base_merge_tree(answers: dict[str, object]):
    """merge_tree_stub behavior keyed on the origin/<base> ref the call targets.
    An exception instance is raised, a CompletedProcess returned."""

    def behavior(args, timeout):
        answer = answers[args[-1]]
        if isinstance(answer, BaseException):
            raise answer
        return answer

    return behavior


_CLEAN = subprocess.CompletedProcess([], 0, b"tree\x00", b"")
_CONFLICT = subprocess.CompletedProcess([], 1, b"tree\x00shared.txt\x00\x00msg", b"")
_TIMEOUT = subprocess.TimeoutExpired(cmd="git", timeout=9)


def test_hook_timed_out_merge_tree_gates_and_prompt_names_the_timeout(
    fixture_repos_conflict_overlap,
    default_branch_name,
    bbw_config_dir,
    monkeypatch,
    capsys,
    merge_tree_stub,
):
    _origin, clone_path = fixture_repos_conflict_overlap
    _configure(clone_path, [default_branch_name])
    seen_timeouts = merge_tree_stub(raise_timeout)
    _patch_no_tty(monkeypatch)

    exit_code = _run_hook(clone_path, default_branch_name, monkeypatch)

    assert exit_code == 0  # no tty: warns and allows
    assert seen_timeouts == [9]
    err = capsys.readouterr().err
    assert "timed out after 9s" in err
    assert "fetch/git error" not in err


def test_hook_real_conflict_still_says_potential_conflicts_when_another_base_timed_out(
    fixture_repos_multi_base, default_branch_name, bbw_config_dir, monkeypatch, merge_tree_stub
):
    origin, clone_path = fixture_repos_multi_base
    _make_release_diverged(origin, default_branch_name)
    _configure(clone_path, [default_branch_name, "release"])
    merge_tree_stub(
        _per_base_merge_tree(
            {f"origin/{default_branch_name}": _CONFLICT, "origin/release": _TIMEOUT}
        )
    )
    prompts = _capture_prompts(monkeypatch)

    exit_code = _run_hook(clone_path, default_branch_name, monkeypatch)

    assert exit_code == 1
    assert len(prompts) == 1
    assert "Potential conflicts detected" in prompts[0]
    assert "timed out after 9s" in prompts[0]


def test_hook_one_base_timed_out_and_one_clean_reports_the_timeout(
    fixture_repos_multi_base, default_branch_name, bbw_config_dir, monkeypatch, merge_tree_stub
):
    origin, clone_path = fixture_repos_multi_base
    _make_release_diverged(origin, default_branch_name)
    _configure(clone_path, [default_branch_name, "release"])
    merge_tree_stub(
        _per_base_merge_tree({f"origin/{default_branch_name}": _CLEAN, "origin/release": _TIMEOUT})
    )
    prompts = _capture_prompts(monkeypatch)

    assert _run_hook(clone_path, default_branch_name, monkeypatch) == 1

    assert "timed out after 9s" in prompts[0]
    assert "Potential conflicts" not in prompts[0]
    assert "fetch/git error" not in prompts[0]


def test_hook_timeout_does_not_hide_a_fetch_failure_on_another_base(
    fixture_repos_multi_base, default_branch_name, bbw_config_dir, monkeypatch, merge_tree_stub
):
    origin, clone_path = fixture_repos_multi_base
    _make_release_diverged(origin, default_branch_name)
    _configure(clone_path, [default_branch_name, "release"])
    merge_tree_stub(
        _per_base_merge_tree(
            {f"origin/{default_branch_name}": _TIMEOUT, "origin/release": _TIMEOUT}
        )
    )
    real_fetch = git_ops.fetch_with_retry
    monkeypatch.setattr(
        git_ops,
        "fetch_with_retry",
        lambda repo_path, base: (
            git_ops.FetchResult(ok=False, error="network down")
            if base == "release"
            else real_fetch(repo_path, base)
        ),
    )
    prompts = _capture_prompts(monkeypatch)

    assert _run_hook(clone_path, default_branch_name, monkeypatch) == 1

    assert "timed out after 9s" in prompts[0]
    assert "fetch/git error" in prompts[0]


def test_hook_undeterminable_merge_tree_says_git_error_and_includes_the_cause(
    fixture_repos_conflict_overlap,
    default_branch_name,
    bbw_config_dir,
    monkeypatch,
    merge_tree_stub,
):
    _origin, clone_path = fixture_repos_conflict_overlap
    _configure(clone_path, [default_branch_name])
    failure = subprocess.CompletedProcess([], 128, b"", b"fatal: simulated merge-tree breakage\n")
    merge_tree_stub(_per_base_merge_tree({f"origin/{default_branch_name}": failure}))
    prompts = _capture_prompts(monkeypatch)

    assert _run_hook(clone_path, default_branch_name, monkeypatch) == 1

    assert "fetch/git error" in prompts[0]
    assert "fatal: simulated merge-tree breakage" in prompts[0]
    assert "network" not in prompts[0]


class _FakeTty(io.StringIO):
    pass


def test_hook_tty_prompt_shows_the_timeout_message_and_honours_the_answer(
    fixture_repos_conflict_overlap,
    default_branch_name,
    bbw_config_dir,
    monkeypatch,
    capsys,
    merge_tree_stub,
):
    _origin, clone_path = fixture_repos_conflict_overlap
    _configure(clone_path, [default_branch_name])
    merge_tree_stub(raise_timeout)
    real_open = builtins.open
    monkeypatch.setattr(
        "builtins.open",
        lambda path, *args, **kwargs: (
            _FakeTty("n\n") if path == "/dev/tty" else real_open(path, *args, **kwargs)
        ),
    )

    exit_code = _run_hook(clone_path, default_branch_name, monkeypatch)

    assert exit_code == 1  # answered "n"
    out = capsys.readouterr().out
    assert "timed out after 9s" in out
    assert "Push anyway? [y/N]" in out


def test_hook_mentions_the_fallback_when_the_configured_timeout_is_invalid(
    fixture_repos_conflict_overlap,
    default_branch_name,
    bbw_config_dir,
    monkeypatch,
    capsys,
    merge_tree_stub,
):
    _origin, clone_path = fixture_repos_conflict_overlap
    bbw_config_dir.mkdir(parents=True, exist_ok=True)
    (bbw_config_dir / "config.json").write_text(
        json.dumps(
            {
                "repos": [
                    {
                        "repo_path": clone_path,
                        "base_branches": [default_branch_name],
                        "merge_tree_timeout_seconds": 0,
                    }
                ]
            }
        )
    )
    seen_timeouts = merge_tree_stub(raise_timeout)
    _patch_no_tty(monkeypatch)

    _run_hook(clone_path, default_branch_name, monkeypatch)

    assert seen_timeouts == [20]
    err = capsys.readouterr().err
    assert "timed out after 20s" in err
    assert "merge_tree_timeout_seconds in config.json is invalid" in err
    assert not (bbw_config_dir / "base-branch-watch.log").exists()

from __future__ import annotations

import json
import os

from base_branch_watch.core import config
from base_branch_watch.core.models import RepoConfig


def test_config_dir_creates_and_returns_path(bbw_config_dir):
    path = config.config_dir()
    assert path.exists()
    assert path.is_dir()
    assert str(path) == str(bbw_config_dir)


def test_save_then_load_round_trips_repo_with_two_base_branches(bbw_config_dir):
    cfg = config.AppConfig(
        poll_interval_seconds=300,
        repos=[RepoConfig(repo_path="/tmp/some-repo", base_branches=["main", "develop"])],
    )
    config.save_config(cfg)

    loaded = config.load_config()

    assert loaded.poll_interval_seconds == 300
    assert len(loaded.repos) == 1
    assert loaded.repos[0].repo_path == "/tmp/some-repo"
    assert loaded.repos[0].base_branches == ["main", "develop"]


def test_load_config_defaults_when_file_missing(bbw_config_dir):
    loaded = config.load_config()
    assert loaded.poll_interval_seconds == config.DEFAULT_POLL_INTERVAL_SECONDS
    assert loaded.repos == []


def test_save_config_is_atomic_via_os_replace(bbw_config_dir):
    cfg = config.AppConfig(repos=[RepoConfig(repo_path="/tmp/r", base_branches=["main"])])
    config.save_config(cfg)

    target = bbw_config_dir / config.CONFIG_FILENAME
    tmp_file = bbw_config_dir / f".{config.CONFIG_FILENAME}.tmp"

    # Final file is valid, complete JSON; no leftover temp file after os.replace.
    assert target.exists()
    assert not tmp_file.exists()
    with open(target) as f:
        data = json.load(f)
    assert data["repos"][0]["repo_path"] == "/tmp/r"


def test_save_config_uses_os_replace(monkeypatch, bbw_config_dir):
    calls = []
    original_replace = os.replace

    def spy_replace(src, dst):
        calls.append((src, dst))
        return original_replace(src, dst)

    monkeypatch.setattr(os, "replace", spy_replace)

    config.save_config(config.AppConfig())

    assert len(calls) == 1


def test_parse_base_branches_splits_strips_dedupes_drops_empties():
    result = config.parse_base_branches("main, develop ,main,, release")
    assert result == ["main", "develop", "release"]


def test_parse_base_branches_whitespace_only_is_empty_list():
    assert config.parse_base_branches("   ") == []


def test_parse_base_branches_single_entry():
    assert config.parse_base_branches("main") == ["main"]


def test_add_repo_appends_new_repo_config():
    cfg = config.AppConfig()
    updated = config.add_repo(cfg, "/tmp/some-repo", ["main"])

    assert len(updated.repos) == 1
    assert updated.repos[0].repo_path == "/tmp/some-repo"
    assert updated.repos[0].base_branches == ["main"]


def test_add_repo_replaces_existing_entry_not_duplicates():
    cfg = config.AppConfig(repos=[RepoConfig(repo_path="/tmp/r", base_branches=["main"])])

    updated = config.add_repo(cfg, "/tmp/r", ["main", "develop"])

    assert len(updated.repos) == 1
    assert updated.repos[0].base_branches == ["main", "develop"]


def test_add_repo_does_not_mutate_input_config():
    cfg = config.AppConfig()
    config.add_repo(cfg, "/tmp/some-repo", ["main"])
    assert cfg.repos == []


def test_remove_repo_drops_matching_leaves_others_untouched():
    cfg = config.AppConfig(
        repos=[
            RepoConfig(repo_path="/tmp/a", base_branches=["main"]),
            RepoConfig(repo_path="/tmp/b", base_branches=["main"]),
        ]
    )

    updated = config.remove_repo(cfg, "/tmp/a")

    assert [r.repo_path for r in updated.repos] == ["/tmp/b"]


def test_remove_repo_absent_path_is_noop():
    cfg = config.AppConfig(repos=[RepoConfig(repo_path="/tmp/a", base_branches=["main"])])

    updated = config.remove_repo(cfg, "/tmp/nonexistent")

    assert [r.repo_path for r in updated.repos] == ["/tmp/a"]

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

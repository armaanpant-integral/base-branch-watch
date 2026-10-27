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


def test_set_poll_interval_updates_value():
    cfg = config.AppConfig(poll_interval_seconds=300)
    updated = config.set_poll_interval(cfg, 600)
    assert updated.poll_interval_seconds == 600


def test_set_poll_interval_clamps_below_floor():
    cfg = config.AppConfig(poll_interval_seconds=300)
    updated = config.set_poll_interval(cfg, 5)
    assert updated.poll_interval_seconds == 30


def test_set_poll_interval_coerces_numeric_string():
    cfg = config.AppConfig(poll_interval_seconds=300)
    updated = config.set_poll_interval(cfg, "120")
    assert updated.poll_interval_seconds == 120


def test_set_poll_interval_does_not_mutate_input_config():
    cfg = config.AppConfig(poll_interval_seconds=300)
    config.set_poll_interval(cfg, 900)
    assert cfg.poll_interval_seconds == 300


def test_load_config_missing_poll_interval_field_defaults_to_300(bbw_config_dir):
    target = config.config_dir() / config.CONFIG_FILENAME
    with open(target, "w") as f:
        json.dump({"repos": []}, f)

    loaded = config.load_config()

    assert loaded.poll_interval_seconds == 300


def test_load_config_corrupt_file_returns_defaults_and_leaves_it_alone_by_default(bbw_config_dir):
    target = config.config_dir() / config.CONFIG_FILENAME
    target.write_text("{not json")

    loaded = config.load_config()

    assert loaded.repos == []
    assert target.read_text() == "{not json"


def test_load_config_quarantine_keeps_a_corrupt_file_out_of_the_next_save(bbw_config_dir):
    target = config.config_dir() / config.CONFIG_FILENAME
    target.write_text("{not json")

    loaded = config.load_config(quarantine=True)
    config.save_config(config.add_repo(loaded, "/tmp/new", ["main"]))

    assert (bbw_config_dir / f"{config.CONFIG_FILENAME}.corrupt").read_text() == "{not json"
    assert config.load_config().repos[0].repo_path == "/tmp/new"


def test_load_config_quarantine_also_moves_aside_a_wrongly_shaped_file(bbw_config_dir):
    target = config.config_dir() / config.CONFIG_FILENAME
    target.write_text(json.dumps({"repos": 5}))

    loaded = config.load_config(quarantine=True)

    assert loaded.repos == []
    assert (bbw_config_dir / f"{config.CONFIG_FILENAME}.corrupt").exists()
    assert not target.exists()


def test_load_config_corruption_is_written_to_the_log(bbw_config_dir):
    from base_branch_watch.core import log

    (config.config_dir() / config.CONFIG_FILENAME).write_text("{not json")

    config.load_config(quarantine=True)

    assert "[FAIL] config.json is not valid JSON" in log.log_path().read_text()


def test_save_config_failure_returns_the_error_instead_of_raising(bbw_config_dir):
    config.config_dir().chmod(0o500)
    try:
        result = config.save_config(config.AppConfig())
    finally:
        config.config_dir().chmod(0o755)

    assert not result.ok
    assert result.error


def test_default_load_config_never_writes_to_the_app_dir_even_on_a_corrupt_file(bbw_config_dir):
    """The one-shot pre-push hook reads config without side effects: no quarantine, no log."""
    from base_branch_watch.core import log

    (config.config_dir() / config.CONFIG_FILENAME).write_text("{not json")
    before = sorted(p.name for p in bbw_config_dir.iterdir())

    config.load_config()

    assert sorted(p.name for p in bbw_config_dir.iterdir()) == before
    assert not log.log_path().exists()

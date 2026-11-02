from __future__ import annotations

import json
import os

import pytest

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


def _write_raw_config(repo_entry: dict) -> None:
    (config.config_dir() / config.CONFIG_FILENAME).write_text(
        json.dumps({"poll_interval_seconds": 300, "repos": [repo_entry]})
    )


def test_merge_tree_timeout_defaults_to_20_when_absent(bbw_config_dir):
    _write_raw_config({"repo_path": "/tmp/r", "base_branches": ["main"]})

    assert config.load_config().repos[0].merge_tree_timeout_seconds == 20


def test_merge_tree_timeout_valid_int_loads_and_round_trips(bbw_config_dir):
    _write_raw_config(
        {"repo_path": "/tmp/r", "base_branches": ["main"], "merge_tree_timeout_seconds": 90}
    )
    loaded = config.load_config()
    assert loaded.repos[0].merge_tree_timeout_seconds == 90

    config.save_config(loaded)

    assert config.load_config().repos[0].merge_tree_timeout_seconds == 90


def test_save_config_omits_the_default_timeout(bbw_config_dir):
    config.save_config(
        config.AppConfig(repos=[RepoConfig(repo_path="/tmp/r", base_branches=["main"])])
    )

    saved = json.loads((config.config_dir() / config.CONFIG_FILENAME).read_text())

    assert "merge_tree_timeout_seconds" not in saved["repos"][0]


@pytest.mark.parametrize("bad", ["90", 90.5, True, 0, -5, 601, None, [90]])
def test_merge_tree_timeout_invalid_value_falls_back_and_is_logged(bbw_config_dir, bad):
    from base_branch_watch.core import log

    _write_raw_config(
        {"repo_path": "/tmp/bad-repo", "base_branches": ["main"], "merge_tree_timeout_seconds": bad}
    )

    loaded = config.load_config(quarantine=True)

    assert loaded.repos[0].merge_tree_timeout_seconds == 20
    logged = log.log_path().read_text()
    assert "/tmp/bad-repo" in logged
    assert repr(bad) in logged


def test_hook_style_load_ignores_invalid_timeout_silently(bbw_config_dir):
    from base_branch_watch.core import log

    _write_raw_config(
        {"repo_path": "/tmp/r", "base_branches": ["main"], "merge_tree_timeout_seconds": 0}
    )

    loaded = config.load_config()

    assert loaded.repos[0].merge_tree_timeout_seconds == 20
    assert not log.log_path().exists()


def test_add_repo_preserves_the_existing_entrys_timeout():
    cfg = config.AppConfig(
        repos=[
            RepoConfig(repo_path="/tmp/r", base_branches=["main"], merge_tree_timeout_seconds=75)
        ]
    )

    updated = config.add_repo(cfg, "/tmp/r", ["main", "develop"])

    assert updated.repos[0].base_branches == ["main", "develop"]
    assert updated.repos[0].merge_tree_timeout_seconds == 75


@pytest.mark.parametrize("edge", [1, 600])
def test_merge_tree_timeout_boundaries_are_accepted(bbw_config_dir, edge):
    _write_raw_config(
        {"repo_path": "/tmp/r", "base_branches": ["main"], "merge_tree_timeout_seconds": edge}
    )

    assert config.load_config().repos[0].merge_tree_timeout_seconds == edge


def test_repo_config_rejects_an_out_of_range_timeout():
    for bad in (0, 601, True, "20", 1.5):
        with pytest.raises(ValueError, match="merge_tree_timeout_seconds"):
            RepoConfig(repo_path="/tmp/r", merge_tree_timeout_seconds=bad)  # type: ignore[arg-type]
    assert (
        RepoConfig(repo_path="/tmp/r", merge_tree_timeout_seconds=600).merge_tree_timeout_seconds
        == 600
    )


def test_non_dict_repo_entry_is_skipped_and_the_rest_survive(bbw_config_dir):
    (config.config_dir()).mkdir(parents=True, exist_ok=True)
    (config.config_dir() / config.CONFIG_FILENAME).write_text(
        json.dumps(
            {
                "repos": [
                    "not-a-dict",
                    {"repo_path": "/tmp/ok", "base_branches": ["main"]},
                ]
            }
        )
    )

    assert [r.repo_path for r in config.load_config().repos] == ["/tmp/ok"]


def test_a_bad_timeout_on_one_repo_leaves_the_others_intact(bbw_config_dir):
    (config.config_dir()).mkdir(parents=True, exist_ok=True)
    (config.config_dir() / config.CONFIG_FILENAME).write_text(
        json.dumps(
            {
                "repos": [
                    {
                        "repo_path": "/tmp/a",
                        "base_branches": ["main"],
                        "merge_tree_timeout_seconds": 90,
                    },
                    {
                        "repo_path": "/tmp/b",
                        "base_branches": ["main"],
                        "merge_tree_timeout_seconds": 0,
                    },
                    {
                        "repo_path": "/tmp/c",
                        "base_branches": ["dev"],
                        "merge_tree_timeout_seconds": 45,
                    },
                ]
            }
        )
    )

    loaded = config.load_config()

    assert [(r.repo_path, r.merge_tree_timeout_seconds) for r in loaded.repos] == [
        ("/tmp/a", 90),
        ("/tmp/b", 20),
        ("/tmp/c", 45),
    ]
    assert set(loaded.timeout_fallbacks) == {"/tmp/b"}


def test_add_repo_for_a_new_repo_gets_the_default_timeout():
    cfg = config.AppConfig(
        repos=[
            RepoConfig(repo_path="/tmp/a", base_branches=["main"], merge_tree_timeout_seconds=75)
        ]
    )

    updated = config.add_repo(cfg, "/tmp/new", ["main"])

    by_path = {r.repo_path: r.merge_tree_timeout_seconds for r in updated.repos}
    assert by_path == {"/tmp/a": 75, "/tmp/new": 20}


def _write_raw(bbw_config_dir, repos):
    bbw_config_dir.mkdir(parents=True, exist_ok=True)
    (bbw_config_dir / config.CONFIG_FILENAME).write_text(json.dumps({"repos": repos}))


@pytest.mark.parametrize("raw_value", [900, "300", 1.5, True])
def test_invalid_timeout_survives_add_repo_save_round_trip(bbw_config_dir, raw_value):
    _write_raw(
        bbw_config_dir,
        [
            {
                "repo_path": "/tmp/a",
                "base_branches": ["main"],
                "merge_tree_timeout_seconds": raw_value,
            }
        ],
    )

    loaded = config.load_config()
    config.save_config(config.add_repo(loaded, "/tmp/b", ["main"]))

    saved = json.loads((bbw_config_dir / config.CONFIG_FILENAME).read_text())
    by_path = {r["repo_path"]: r for r in saved["repos"]}
    assert by_path["/tmp/a"]["merge_tree_timeout_seconds"] == raw_value
    assert "merge_tree_timeout_seconds" not in by_path["/tmp/b"]


def test_invalid_timeout_survives_set_poll_interval_and_re_adding_same_repo(bbw_config_dir):
    _write_raw(
        bbw_config_dir,
        [{"repo_path": "/tmp/a", "base_branches": ["main"], "merge_tree_timeout_seconds": 900}],
    )

    loaded = config.load_config()
    updated = config.add_repo(config.set_poll_interval(loaded, 60), "/tmp/a", ["develop"])
    config.save_config(updated)

    saved = json.loads((bbw_config_dir / config.CONFIG_FILENAME).read_text())
    assert saved["repos"][0]["merge_tree_timeout_seconds"] == 900
    assert saved["repos"][0]["base_branches"] == ["develop"]


def test_remove_repo_drops_its_raw_timeout_but_keeps_others(bbw_config_dir):
    _write_raw(
        bbw_config_dir,
        [
            {"repo_path": "/tmp/a", "base_branches": ["main"], "merge_tree_timeout_seconds": 900},
            {"repo_path": "/tmp/b", "base_branches": ["main"], "merge_tree_timeout_seconds": "x"},
        ],
    )

    updated = config.remove_repo(config.load_config(), "/tmp/a")
    config.save_config(updated)

    saved = json.loads((bbw_config_dir / config.CONFIG_FILENAME).read_text())
    assert [(r["repo_path"], r["merge_tree_timeout_seconds"]) for r in saved["repos"]] == [
        ("/tmp/b", "x")
    ]


def test_explicit_timeout_edit_replaces_the_preserved_raw_value(bbw_config_dir):
    _write_raw(
        bbw_config_dir,
        [{"repo_path": "/tmp/a", "base_branches": ["main"], "merge_tree_timeout_seconds": 900}],
    )
    loaded = config.load_config()
    edited = config.AppConfig(
        repos=[
            RepoConfig(repo_path="/tmp/a", base_branches=["main"], merge_tree_timeout_seconds=45)
        ],
        timeout_fallbacks=loaded.timeout_fallbacks,
    )

    config.save_config(edited)

    saved = json.loads((bbw_config_dir / config.CONFIG_FILENAME).read_text())
    assert saved["repos"][0]["merge_tree_timeout_seconds"] == 45


def test_load_skips_entry_with_unhashable_repo_path_instead_of_quarantining(bbw_config_dir):
    _write_raw(
        bbw_config_dir,
        [
            {"repo_path": ["not", "a", "str"], "base_branches": ["main"]},
            {"repo_path": "/tmp/ok", "base_branches": ["main"]},
        ],
    )

    loaded = config.load_config()

    assert [r.repo_path for r in loaded.repos] == ["/tmp/ok"]


def _saved_repos(bbw_config_dir):
    return json.loads((bbw_config_dir / config.CONFIG_FILENAME).read_text())["repos"]


@pytest.mark.parametrize(
    "skipped",
    ["not-a-dict", {"base_branches": ["main"]}, {"repo_path": 7}, {"repo_path": ["x"]}],
)
def test_skipped_entry_survives_add_remove_and_interval_saves(bbw_config_dir, skipped):
    _write_raw(bbw_config_dir, [skipped, {"repo_path": "/tmp/ok", "base_branches": ["main"]}])

    loaded = config.load_config()
    assert loaded.skipped_entries == [skipped]
    for edited in (
        config.add_repo(loaded, "/tmp/b", ["main"]),
        config.remove_repo(loaded, "/tmp/ok"),
        config.set_poll_interval(loaded, 60),
    ):
        config.save_config(edited)
        assert skipped in _saved_repos(bbw_config_dir)


def test_skip_is_reported_with_the_existing_log_pattern(bbw_config_dir):
    from base_branch_watch.core import log

    _write_raw(bbw_config_dir, ["not-a-dict"])

    config.load_config(quarantine=True)

    assert "config: skipping non-object repo entry 'not-a-dict'" in log.log_path().read_text()


def test_duplicate_repo_path_first_wins_and_duplicate_is_kept_untouched(bbw_config_dir):
    first = {"repo_path": "/tmp/a", "base_branches": ["main"], "merge_tree_timeout_seconds": 900}
    dup = {"repo_path": "/tmp/a", "base_branches": ["dev"], "merge_tree_timeout_seconds": 30}
    _write_raw(bbw_config_dir, [first, dup])

    loaded = config.load_config()

    assert [r.base_branches for r in loaded.repos] == [["main"]]
    assert loaded.timeout_fallbacks == {"/tmp/a": 900}  # not overwritten by the duplicate
    config.save_config(config.set_poll_interval(loaded, 60))
    assert _saved_repos(bbw_config_dir) == [first, dup]


def test_removing_a_repo_also_drops_its_duplicate_so_it_does_not_reappear(bbw_config_dir):
    entry = {"repo_path": "/tmp/a", "base_branches": ["main"]}
    _write_raw(bbw_config_dir, [entry, dict(entry)])

    config.save_config(config.remove_repo(config.load_config(), "/tmp/a"))

    assert _saved_repos(bbw_config_dir) == []


def test_unknown_keys_on_a_kept_entry_survive_a_save(bbw_config_dir):
    _write_raw(bbw_config_dir, [{"repo_path": "/tmp/a", "base_branches": ["main"], "note": "hi"}])

    config.save_config(config.add_repo(config.load_config(), "/tmp/b", ["main"]))

    assert {r["repo_path"]: r.get("note") for r in _saved_repos(bbw_config_dir)} == {
        "/tmp/a": "hi",
        "/tmp/b": None,
    }


def test_fallback_log_line_truncates_a_huge_raw_value(bbw_config_dir):
    from base_branch_watch.core import log

    _write_raw(
        bbw_config_dir,
        [{"repo_path": "/tmp/a", "base_branches": [], "merge_tree_timeout_seconds": "x" * 5000}],
    )

    config.load_config(quarantine=True)

    logged = log.log_path().read_text()
    assert "x" * 90 in logged and "x" * 150 not in logged

"""core.store: the app data dir and its JSON files (atomic write, tolerant read)."""

from __future__ import annotations

import json
import os

from base_branch_watch.core import store


def _reports() -> tuple[list[str], object]:
    lines: list[str] = []
    return lines, lines.append


# -- config_dir ------------------------------------------------------------------


def test_config_dir_creates_and_returns_the_override_path(bbw_config_dir):
    path = store.config_dir()

    assert path == bbw_config_dir
    assert path.is_dir()


# -- read_object -----------------------------------------------------------------


def test_read_object_missing_file_is_none_and_silent(bbw_config_dir):
    lines, report = _reports()

    assert store.read_object("x.json", report=report) is None
    assert lines == []


def test_read_object_returns_the_parsed_dict(bbw_config_dir):
    (store.config_dir() / "x.json").write_text('{"a": 1}')

    assert store.read_object("x.json") == {"a": 1}


def test_read_object_unparseable_file_is_reported_and_left_in_place_by_default(bbw_config_dir):
    path = store.config_dir() / "x.json"
    path.write_text("{not json")
    lines, report = _reports()

    assert store.read_object("x.json", report=report) is None

    assert path.read_text() == "{not json"
    assert len(lines) == 1
    assert lines[0].startswith("[FAIL] x.json is not valid JSON")


def test_read_object_non_object_json_counts_as_corrupt(bbw_config_dir):
    (store.config_dir() / "x.json").write_text("[1, 2]")
    lines, report = _reports()

    assert store.read_object("x.json", report=report) is None
    assert "not a JSON object" in lines[0]


def test_read_object_quarantine_moves_a_corrupt_file_aside(bbw_config_dir):
    path = store.config_dir() / "x.json"
    path.write_text("{not json")
    lines, report = _reports()

    assert store.read_object("x.json", quarantine=True, report=report) is None

    assert not path.exists()
    assert (store.config_dir() / "x.json.corrupt").read_text() == "{not json"
    assert any("moved to x.json.corrupt" in line for line in lines)


def test_quarantine_keeps_only_the_latest_corrupt_copy(bbw_config_dir):
    path = store.config_dir() / "x.json"
    path.write_text("first bad")
    store.read_object("x.json", quarantine=True)
    path.write_text("second bad")

    store.read_object("x.json", quarantine=True)

    assert (store.config_dir() / "x.json.corrupt").read_text() == "second bad"


def test_quarantine_does_not_touch_a_valid_file(bbw_config_dir):
    path = store.config_dir() / "x.json"
    path.write_text('{"ok": true}')

    assert store.read_object("x.json", quarantine=True) == {"ok": True}
    assert path.exists()
    assert not (store.config_dir() / "x.json.corrupt").exists()


def test_read_object_unreadable_file_is_reported_not_quarantined(bbw_config_dir):
    path = store.config_dir() / "x.json"
    path.write_text("{}")
    path.chmod(0o000)
    lines, report = _reports()
    try:
        assert store.read_object("x.json", quarantine=True, report=report) is None
    finally:
        path.chmod(0o644)

    assert path.exists(), "a permission problem is not corruption"
    assert lines and lines[0].startswith("[FAIL] could not read x.json")


def test_quarantine_file_is_public_for_callers_that_find_a_bad_shape(bbw_config_dir):
    path = store.config_dir() / "x.json"
    path.write_text('{"repos": 5}')
    lines, report = _reports()

    store.quarantine_file("x.json", "unexpected shape", report=report)

    assert not path.exists()
    assert (store.config_dir() / "x.json.corrupt").exists()
    assert "unexpected shape" in lines[0]


# -- write_object ----------------------------------------------------------------


def test_write_object_round_trips(bbw_config_dir):
    result = store.write_object("x.json", {"a": [1, 2]})

    assert result.ok and result.error is None
    assert json.loads((store.config_dir() / "x.json").read_text()) == {"a": [1, 2]}


def test_write_object_is_atomic_no_temp_file_left_behind(bbw_config_dir):
    store.write_object("x.json", {"a": 1})

    assert not (store.config_dir() / ".x.json.tmp").exists()


def test_write_object_replaces_via_os_replace(monkeypatch, bbw_config_dir):
    calls = []
    real_replace = os.replace
    monkeypatch.setattr(os, "replace", lambda s, d: (calls.append((s, d)), real_replace(s, d))[1])

    store.write_object("x.json", {})

    assert len(calls) == 1


def test_write_object_failure_is_reported_never_raised_and_keeps_the_old_file(bbw_config_dir):
    store.write_object("x.json", {"v": 1})
    config_dir = store.config_dir()
    config_dir.chmod(0o500)  # no new files
    lines, report = _reports()
    try:
        result = store.write_object("x.json", {"v": 2}, report=report)
    finally:
        config_dir.chmod(0o755)

    assert not result.ok
    assert result.error
    assert lines and lines[0].startswith("[FAIL] could not save x.json")
    assert json.loads((config_dir / "x.json").read_text()) == {"v": 1}
    assert not (config_dir / ".x.json.tmp").exists()


def test_write_object_cleans_up_the_temp_file_when_replace_fails(monkeypatch, bbw_config_dir):
    def broken_replace(src, dst):
        raise OSError("disk on fire")

    monkeypatch.setattr(os, "replace", broken_replace)

    result = store.write_object("x.json", {"v": 1})

    assert not result.ok
    assert "disk on fire" in result.error
    assert not (store.config_dir() / ".x.json.tmp").exists()

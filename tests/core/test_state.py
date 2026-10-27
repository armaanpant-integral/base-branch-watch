"""Tests for core.state - state.json persistence (dedupe rules: test_notify_policy)."""

from __future__ import annotations

from base_branch_watch.core import notify_policy, state


def test_save_state_and_load_state_round_trip(bbw_config_dir):
    s: state.State = {
        "/repo-a": {"main": "deadbeef", "release": "cafef00d"},
        "/repo-b": {"main": "abc1234"},
    }

    state.save_state(s)

    assert state.load_state() == s


def test_save_state_and_load_state_round_trip_includes_reserved_keys(bbw_config_dir):
    s: state.State = {
        "/repo-a": {notify_policy.UNPUSHED_KEY: "4"},
        notify_policy.SELF_UPDATE_KEY: {"sha": "abc"},
    }

    state.save_state(s)

    assert state.load_state() == s


def test_load_state_returns_empty_dict_when_no_file_exists(bbw_config_dir):
    assert state.load_state() == {}


def test_save_state_writes_atomically_via_temp_file_and_replace(bbw_config_dir):
    """Matches core.config's atomic-write pattern - no partial-write file left behind."""
    state.save_state({"/repo-a": {"main": "deadbeef"}})

    target = bbw_config_dir / "state.json"
    tmp = bbw_config_dir / ".state.json.tmp"
    assert target.exists()
    assert not tmp.exists()


def test_load_state_corrupt_file_is_empty_and_quarantined_on_request(bbw_config_dir):
    target = bbw_config_dir / "state.json"
    bbw_config_dir.mkdir(parents=True, exist_ok=True)
    target.write_text("[1]")

    assert state.load_state() == {}
    assert target.exists()

    assert state.load_state(quarantine=True) == {}
    assert (bbw_config_dir / "state.json.corrupt").exists()


def test_save_state_failure_returns_the_error_instead_of_raising(bbw_config_dir):
    state.load_state()  # creates the dir
    bbw_config_dir.chmod(0o500)
    try:
        result = state.save_state({"/r": {"main": "abc"}})
    finally:
        bbw_config_dir.chmod(0o755)

    assert not result.ok

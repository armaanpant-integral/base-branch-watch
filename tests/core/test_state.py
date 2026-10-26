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

"""Tests for core.state — per-(repo,base) last-notified-SHA dedupe persistence."""

from __future__ import annotations

from base_branch_watch.core import state


def test_should_notify_true_first_time_sha_is_seen():
    s: state.State = {}
    assert state.should_notify(s, "/repo", "main", "sha1") is True


def test_should_notify_false_after_mark_notified_for_same_sha():
    s: state.State = {}
    assert state.should_notify(s, "/repo", "main", "sha1") is True
    s = state.mark_notified(s, "/repo", "main", "sha1")
    assert state.should_notify(s, "/repo", "main", "sha1") is False


def test_should_notify_true_again_when_sha_changes():
    s: state.State = {}
    s = state.mark_notified(s, "/repo", "main", "sha1")
    assert state.should_notify(s, "/repo", "main", "sha1") is False
    assert state.should_notify(s, "/repo", "main", "sha2") is True


def test_mark_notified_is_scoped_per_repo_and_base():
    s: state.State = {}
    s = state.mark_notified(s, "/repo-a", "main", "sha1")
    # A different repo, or a different base on the same repo, is unaffected.
    assert state.should_notify(s, "/repo-b", "main", "sha1") is True
    assert state.should_notify(s, "/repo-a", "develop", "sha1") is True
    assert state.should_notify(s, "/repo-a", "main", "sha1") is False


def test_save_state_and_load_state_round_trip(bbw_config_dir):
    s: state.State = {}
    s = state.mark_notified(s, "/repo-a", "main", "deadbeef")
    s = state.mark_notified(s, "/repo-a", "release", "cafef00d")
    s = state.mark_notified(s, "/repo-b", "main", "abc1234")

    state.save_state(s)
    loaded = state.load_state()

    assert loaded == {
        "/repo-a": {"main": "deadbeef", "release": "cafef00d"},
        "/repo-b": {"main": "abc1234"},
    }


def test_load_state_returns_empty_dict_when_no_file_exists(bbw_config_dir):
    assert state.load_state() == {}


def test_save_state_writes_atomically_via_temp_file_and_replace(bbw_config_dir):
    """Matches core.config's atomic-write pattern — no partial-write file left behind."""
    s: state.State = {}
    s = state.mark_notified(s, "/repo-a", "main", "deadbeef")
    state.save_state(s)

    target = bbw_config_dir / "state.json"
    tmp = bbw_config_dir / ".state.json.tmp"
    assert target.exists()
    assert not tmp.exists()

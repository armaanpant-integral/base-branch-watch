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


def test_should_notify_unpushed_true_first_time_count_is_seen():
    s: state.State = {}
    assert state.should_notify_unpushed(s, "/repo", 3) is True


def test_should_notify_unpushed_false_after_mark_notified_for_same_count():
    s: state.State = {}
    s = state.mark_notified_unpushed(s, "/repo", 3)
    assert state.should_notify_unpushed(s, "/repo", 3) is False


def test_should_notify_unpushed_true_again_when_count_changes():
    s: state.State = {}
    s = state.mark_notified_unpushed(s, "/repo", 3)
    assert state.should_notify_unpushed(s, "/repo", 5) is True


def test_clear_notified_unpushed_lets_a_repeated_count_re_notify():
    """The exact scenario WR-01 fixes: unpushed drops to 0, then climbs back
    to a count that was already notified before — must re-fire, not stay deduped."""
    s: state.State = {}
    s = state.mark_notified_unpushed(s, "/repo", 3)
    assert state.should_notify_unpushed(s, "/repo", 3) is False

    s = state.clear_notified_unpushed(s, "/repo")
    assert state.should_notify_unpushed(s, "/repo", 3) is True


def test_clear_notified_unpushed_is_a_noop_when_nothing_was_stored():
    s: state.State = {}
    s = state.clear_notified_unpushed(s, "/repo")
    assert s == {}


def test_unpushed_key_is_scoped_per_repo_and_independent_of_base_sha_dedupe():
    s: state.State = {}
    s = state.mark_notified(s, "/repo-a", "main", "deadbeef")
    s = state.mark_notified_unpushed(s, "/repo-a", 2)
    # SHA dedupe and unpushed dedupe track independently for the same repo.
    assert state.should_notify(s, "/repo-a", "main", "deadbeef") is False
    assert state.should_notify_unpushed(s, "/repo-a", 2) is False
    assert state.should_notify_unpushed(s, "/repo-a", 4) is True
    # A different repo is unaffected.
    assert state.should_notify_unpushed(s, "/repo-b", 2) is True


def test_save_state_and_load_state_round_trip_includes_unpushed_key(bbw_config_dir):
    s: state.State = {}
    s = state.mark_notified_unpushed(s, "/repo-a", 4)
    state.save_state(s)
    loaded = state.load_state()
    assert loaded == {"/repo-a": {state.UNPUSHED_KEY: "4"}}

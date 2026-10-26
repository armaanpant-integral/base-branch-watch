"""Pure tests for core.notify_policy - no git, no rumps, no disk.

The policy takes (persisted state, fresh statuses) and returns what to send
plus the next state; the caller sends then persists when next_state differs.
"""

import copy

from base_branch_watch.core import notify_policy
from base_branch_watch.core.models import BranchStatus, RepoStatus, StatusKind


def _base(base="main", kind=StatusKind.BEHIND, sha="sha1", behind=2, **kw):
    return BranchStatus(
        base=base, behind=behind, ahead_of_base=0, kind=kind, head_sha=sha, **kw
    )


def _repo(path="/repo", bases=None, unpushed=0, failure_reason=None):
    return RepoStatus(
        repo_path=path,
        name=path.rsplit("/", 1)[-1],
        current_branch="feature",
        unpushed=unpushed,
        branch_statuses=[_base()] if bases is None else bases,
        failure_reason=failure_reason,
    )


def _up_to_date(base="main", sha="sha1"):
    return _base(base=base, kind=StatusKind.UP_TO_DATE, sha=sha, behind=0)


def _failed_base(base="main"):
    return BranchStatus(
        base=base,
        behind=0,
        ahead_of_base=0,
        kind=StatusKind.CHECK_FAILED,
        reason="fetch failed",
    )


def _paths(decision):
    return [s.repo_path for s in decision.statuses]


# -- base-SHA axis -----------------------------------------------------------


def test_all_ok_notifies_nothing_and_leaves_state_unchanged():
    state = {}
    decision = notify_policy.decide_digest(state, [_repo(bases=[_up_to_date()])])
    assert decision.statuses == []
    assert decision.state == state


def test_new_base_sha_notifies_then_dedupes_on_the_returned_state():
    first = notify_policy.decide_digest({}, [_repo()])
    assert _paths(first) == ["/repo"]
    assert first.state == {"/repo": {"main": "sha1"}}

    second = notify_policy.decide_digest(first.state, [_repo()])
    assert second.statuses == []
    assert second.state == first.state


def test_changed_base_sha_notifies_again():
    state = {"/repo": {"main": "sha1"}}
    decision = notify_policy.decide_digest(state, [_repo(bases=[_base(sha="sha2")])])
    assert _paths(decision) == ["/repo"]
    assert decision.state == {"/repo": {"main": "sha2"}}


def test_check_failed_base_always_notifies_and_records_nothing_for_it():
    state = {}
    first = notify_policy.decide_digest(state, [_repo(bases=[_failed_base()])])
    assert _paths(first) == ["/repo"]
    assert first.state == {}
    again = notify_policy.decide_digest(first.state, [_repo(bases=[_failed_base()])])
    assert _paths(again) == ["/repo"]


def test_repo_level_failure_always_notifies_and_leaves_state_unchanged():
    status = _repo(bases=[], failure_reason="repo missing")
    first = notify_policy.decide_digest({}, [status])
    again = notify_policy.decide_digest(first.state, [status])
    assert _paths(first) == _paths(again) == ["/repo"]
    assert again.state == {}


def test_unresolved_sha_on_a_non_failed_base_does_not_trigger_a_notification():
    status = _repo(bases=[_base(sha=None)])
    decision = notify_policy.decide_digest({}, [status])
    assert decision.statuses == []
    assert decision.state == {}


def test_notifying_records_every_resolved_base_sha_not_just_the_trigger():
    state = {"/repo": {"main": "sha1"}}  # main already notified
    status = _repo(bases=[_base("main", sha="sha1"), _base("release", sha="r1")])
    decision = notify_policy.decide_digest(state, [status])
    assert _paths(decision) == ["/repo"]
    assert decision.state == {"/repo": {"main": "sha1", "release": "r1"}}


def test_state_is_scoped_per_repo():
    state = {"/repo-a": {"main": "sha1"}}
    decision = notify_policy.decide_digest(
        state, [_repo("/repo-a"), _repo("/repo-b")]
    )
    assert _paths(decision) == ["/repo-b"]


# -- unpushed axis -----------------------------------------------------------


def test_unpushed_only_repo_notifies_when_count_changes():
    status = lambda n: _repo(bases=[_up_to_date()], unpushed=n)  # noqa: E731
    first = notify_policy.decide_digest({}, [status(3)])
    assert _paths(first) == ["/repo"]

    same = notify_policy.decide_digest(first.state, [status(3)])
    assert same.statuses == []

    grown = notify_policy.decide_digest(first.state, [status(5)])
    assert _paths(grown) == ["/repo"]


def test_unpushed_returning_to_zero_clears_the_stored_count_in_next_state():
    state = {"/repo": {notify_policy.UNPUSHED_KEY: "3"}}
    decision = notify_policy.decide_digest(state, [_repo(bases=[_up_to_date()])])
    assert decision.statuses == []
    # The persisted-clear bug: next_state must differ from state so the caller saves.
    assert decision.state != state
    assert notify_policy.UNPUSHED_KEY not in decision.state.get("/repo", {})

    regrown = notify_policy.decide_digest(
        decision.state, [_repo(bases=[_up_to_date()], unpushed=3)]
    )
    assert _paths(regrown) == ["/repo"]


def test_unpushed_and_sha_axes_are_independent():
    state = {"/repo": {"main": "sha1"}}
    status = _repo(bases=[_base(sha="sha1")], unpushed=2)
    decision = notify_policy.decide_digest(state, [status])
    assert _paths(decision) == ["/repo"]  # unpushed alone triggered it
    assert decision.state["/repo"][notify_policy.UNPUSHED_KEY] == "2"


# -- purity ------------------------------------------------------------------


def test_decide_digest_does_not_mutate_the_input_state():
    state = {"/repo": {"main": "old", notify_policy.UNPUSHED_KEY: "1"}}
    snapshot = copy.deepcopy(state)
    notify_policy.decide_digest(state, [_repo(bases=[_base(sha="new")])])
    notify_policy.decide_digest(state, [_repo(bases=[_up_to_date()])])
    assert state == snapshot


# -- self-update axis --------------------------------------------------------


def test_update_notifies_once_per_remote_sha():
    first = notify_policy.decide_update({}, "sha1")
    assert first.notify is True
    assert first.state != {}

    again = notify_policy.decide_update(first.state, "sha1")
    assert again.notify is False
    assert again.state == first.state

    newer = notify_policy.decide_update(first.state, "sha2")
    assert newer.notify is True


def test_decide_update_does_not_mutate_the_input_state():
    state = {}
    notify_policy.decide_update(state, "sha1")
    assert state == {}

"""Notification dedupe policy - pure: (state, statuses) -> (what to send, next state).

Three independent axes decide whether the user hears about something again:
the base-branch SHA per (repo, base), the repo's unpushed-commit count
(WR-01), and the app's own upstream SHA (self-update). The caller sends what
this returns, then persists `next_state` whenever it differs from the state it
passed in. Nothing here touches git, disk, or rumps, and inputs are never
mutated.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field

from base_branch_watch.core.models import RepoStatus, Severity, StatusKind
from base_branch_watch.core.state import State

# Reserved per-repo key for the last-notified unpushed count (WR-01). A NUL
# byte can never appear in a git ref/branch name, so this key can never
# collide with a real configured base branch.
UNPUSHED_KEY = "\x00unpushed"

# App-level (not per-repo) slot for the last-notified self-update SHA. Same
# NUL-prefix collision guarantee as UNPUSHED_KEY, so it can never clash with a
# real watched repo path.
SELF_UPDATE_KEY = "\x00self-update"


@dataclass(frozen=True)
class DigestDecision:
    statuses: list[RepoStatus] = field(default_factory=list)
    """Statuses to put in this cycle's digest notification (empty = send nothing)."""
    state: State = field(default_factory=dict)
    """State to persist after sending; equal to the input when nothing changed."""


@dataclass(frozen=True)
class UpdateDecision:
    notify: bool
    state: State


def decide_digest(state: State, statuses: list[RepoStatus]) -> DigestDecision:
    """Pick the statuses needing attention whose dedupe says "notify now".

    Repo-level or per-base CHECK_FAILED has no SHA to dedupe against, so it
    always counts while it keeps failing. A repo that notifies has every
    resolved base SHA and its unpushed count recorded, so an unchanged repeat
    cycle stays quiet. Unpushed returning to 0 drops the stored count so a
    later identical count re-notifies - and shows up as a state change the
    caller must persist.
    """
    next_state = copy.deepcopy(state)
    to_send: list[RepoStatus] = []

    for status in statuses:
        if status.failure_reason is not None:
            to_send.append(status)
            continue

        repo_state = next_state.get(status.repo_path, {})
        if status.unpushed == 0:
            # Cleared even for an all-OK repo (the common "just pushed" case),
            # otherwise a stale count would swallow the next identical one.
            repo_state.pop(UNPUSHED_KEY, None)

        if status.severity == Severity.OK:
            continue

        needs = False
        base_shas: dict[str, str] = {}
        for bs in status.branch_statuses:
            if bs.kind == StatusKind.CHECK_FAILED:
                needs = True
                continue
            if bs.head_sha is None:
                continue
            base_shas[bs.base] = bs.head_sha
            if repo_state.get(bs.base) != bs.head_sha:
                needs = True

        if status.unpushed > 0 and repo_state.get(UNPUSHED_KEY) != str(status.unpushed):
            needs = True

        if needs:
            to_send.append(status)
            recorded = dict(base_shas)
            if status.unpushed > 0:
                recorded[UNPUSHED_KEY] = str(status.unpushed)
            if recorded:
                next_state.setdefault(status.repo_path, {}).update(recorded)

    return DigestDecision(statuses=to_send, state=next_state)


def decide_update(state: State, remote_sha: str) -> UpdateDecision:
    """Notify once per new upstream SHA of the app's own checkout."""
    if state.get(SELF_UPDATE_KEY, {}).get("sha") == remote_sha:
        return UpdateDecision(notify=False, state=state)
    next_state = copy.deepcopy(state)
    next_state.setdefault(SELF_UPDATE_KEY, {})["sha"] = remote_sha
    return UpdateDecision(notify=True, state=next_state)

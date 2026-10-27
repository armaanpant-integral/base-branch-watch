"""Per-(repo,base) last-notified-SHA persistence - the app's own dedupe state.

State is app-owned, single-writer (ARCHITECTURE.md Key Data Flows point 2) -
the pre-push hook (a separate, one-shot process) never reads or writes this
file; only app/menubar.py's polling cycle does. Reads and the atomic write live in
core.store. What the stored values mean (dedupe rules, reserved keys) lives in
core.notify_policy.
"""

from __future__ import annotations

from base_branch_watch.core import log, store
from base_branch_watch.core.store import WriteResult

STATE_FILENAME = "state.json"

# {repo_path: {base: last_notified_sha}}, plus the reserved NUL-prefixed keys
# core.notify_policy stores alongside them (unpushed count, self-update SHA).
State = dict[str, dict[str, str]]


def load_state(*, quarantine: bool = False) -> State:
    """Load State from disk, returning {} if the file is missing or unusable."""
    raw = store.read_object(STATE_FILENAME, quarantine=quarantine, report=log.append)
    return {} if raw is None else raw


def save_state(state: State) -> WriteResult:
    """Atomically persist State. Never raises; the result says whether it was saved."""
    return store.write_object(STATE_FILENAME, state, report=log.append)

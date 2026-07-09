"""Notifier protocol — swappable notification delivery.

ARCHITECTURE.md's "notify/ is a swappable interface, not a hardcoded
osascript call" rationale: defining this now means a future
UNUserNotificationCenter implementation (blocked on a signed .app bundle,
see CLAUDE.md Packaging) is a new implementation, not a rewrite of polling
logic. Implementations receive the full list of RepoStatus needing
attention and must fire exactly one notification per cycle (send_digest),
never one per repo (ARCHITECTURE.md Anti-Pattern 4 / NOTIFY-01).
"""

from __future__ import annotations

from typing import Protocol

from base_branch_watch.core.models import RepoStatus


class Notifier(Protocol):
    def send_digest(self, statuses: list[RepoStatus]) -> None: ...

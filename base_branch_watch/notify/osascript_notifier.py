"""osascript-backed digest Notifier — fires exactly one notification per
polling cycle listing every repo needing attention (never one per repo,
ARCHITECTURE.md Anti-Pattern 4 / NOTIFY-01, T-4 threat register).

NOTE: `terminal-notifier` was evaluated as an alternative (to fix osascript's
lack of a distinct app identity — clicking a notification activates whatever
process ran it, commonly "Script Editor", instead of showing anything
useful) and rejected: it's unmaintained since 2017 and confirmed
non-functional on macOS 26 in manual testing (no permission prompt, no
notification registered, silent no-op). Sticking with osascript for v1 per
CLAUDE.md's constraint — the click-through issue is a known, accepted
cosmetic limitation, not a functional bug this notifier needs to solve.
Revisit only via the v2 signed .app bundle + UNUserNotificationCenter path.
"""

from __future__ import annotations

import subprocess

from base_branch_watch.core import status_text
from base_branch_watch.core.models import RepoStatus

_MAX_BODY_CHARS = 300


def _escape(text: str) -> str:
    """Escape characters that would break the AppleScript string literal
    this module builds (T-4-01 mitigation) — backslash first, then quotes."""
    return text.replace("\\", "\\\\").replace('"', '\\"')


def _build_body(lines: list[str]) -> str:
    """Newline-join lines, truncated to a ~300-char budget with a trailing
    "…and {remaining} more" line when lines had to be dropped."""
    joined = "\n".join(lines)
    if len(joined) <= _MAX_BODY_CHARS:
        return joined

    kept: list[str] = []
    length = 0
    for line in lines:
        added = len(line) + (1 if kept else 0)
        if length + added > _MAX_BODY_CHARS:
            break
        kept.append(line)
        length += added

    remaining = len(lines) - len(kept)
    kept.append(f"…and {remaining} more")
    return "\n".join(kept)


class OsascriptNotifier:
    """Notifier implementation firing one osascript display-notification banner."""

    def send_digest(self, statuses: list[RepoStatus]) -> None:
        if not statuses:
            return

        n = len(statuses)
        title = f"{n} repo needs attention" if n == 1 else f"{n} repos need attention"
        subtitle = "Base Branch Watch"
        lines = [f"{status.name}: {status_text.repo_summary(status)}" for status in statuses]
        body = _build_body(lines)

        self._display(body, title, subtitle)

    def send_message(self, title: str, body: str) -> None:
        """One-off banner not tied to any repo (e.g. "update available")."""
        self._display(_build_body(body.splitlines()), title, "Base Branch Watch")

    @staticmethod
    def _display(body: str, title: str, subtitle: str) -> None:
        script = (
            f'display notification "{_escape(body)}" with title "{_escape(title)}" '
            f'subtitle "{_escape(subtitle)}" sound name "Glass"'
        )
        try:
            subprocess.run(["osascript", "-e", script], timeout=10)
        except (subprocess.TimeoutExpired, OSError):
            # Mirror core/git_ops.py's convention: a hung/missing osascript
            # binary must never raise out of a timer-driven poll cycle.
            pass

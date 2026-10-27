"""In-memory `GhClient` adapter for tests: canned outcomes keyed by gh args."""

from __future__ import annotations

from base_branch_watch.core.gh_client import GhOutcome


def ok(stdout: str = "") -> GhOutcome:
    return GhOutcome(returncode=0, stdout=stdout, stderr="")


def fail(returncode: int = 1, stderr: str = "") -> GhOutcome:
    return GhOutcome(returncode=returncode, stdout="", stderr=stderr)


def broken(error: str) -> GhOutcome:
    """Timeout / OSError: gh never produced an exit status."""
    return GhOutcome(returncode=-1, stdout="", stderr="", error=error)


class FakeGhClient:
    """`responses` maps a gh arg tuple (e.g. ("pr", "view", "--json", ...)) to an outcome.

    Matching is by the leading args, so a test can key on ("pr", "checks") without
    spelling out the field list. An unmatched call fails loudly.
    """

    def __init__(self, responses: dict[tuple[str, ...], GhOutcome], installed: bool = True):
        self.installed = installed
        self._responses = responses
        self.calls: list[tuple[str | None, tuple[str, ...]]] = []

    def run(self, repo_path: str | None, args: list[str], timeout: int) -> GhOutcome:
        self.calls.append((repo_path, tuple(args)))
        if not self.installed:
            return broken("gh is not installed")
        for prefix, outcome in self._responses.items():
            if tuple(args[: len(prefix)]) == prefix:
                return outcome
        raise AssertionError(f"FakeGhClient: no canned response for gh {args}")

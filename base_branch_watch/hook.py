"""python3 -m base_branch_watch.hook -- pre-push conflict gate entry point.

Thin porcelain, no duplicated logic (D-01 / ARCH-01): all git access and
pre-push stdin-protocol parsing go through `core.git_ops`. The only pieces
that live here are the two genuinely side-effecting, non-testable-in-
isolation operations the Architectural Responsibility Map assigns to the
porcelain tier -- the interactive /dev/tty prompt and the BBWATCH_SKIP_GATE
env-var escape hatch (D-08/D-09).

Installed as a thin shell wrapper's exec target: `python3 -m
base_branch_watch.hook "$@" < /dev/stdin` (argv = [remote_name, remote_url]
per git's pre-push protocol; the ref-update lines arrive on stdin, NOT argv).
"""

from __future__ import annotations

import os
import sys

from base_branch_watch.core import base_verdict, config, git_ops, hook_summary
from base_branch_watch.core.models import IncomingCommit


def prompt_push_anyway(message: str) -> bool:
    """D-08/D-09: /dev/tty prompt with an env-var escape hatch.

    BBWATCH_SKIP_GATE=1 bypasses entirely, without ever opening /dev/tty.
    Otherwise reads the confirmation from /dev/tty -- never sys.stdin, which
    git repurposes for ref-update data during pre-push. If /dev/tty cannot
    be opened at all (no controlling terminal, e.g. a push run by an IDE or
    agent), nobody can answer: print the warning to stderr and allow the push
    rather than force a blanket `--no-verify`.
    """
    if os.environ.get("BBWATCH_SKIP_GATE") == "1":
        return True
    try:
        with open("/dev/tty") as tty:
            print(f"{message}\nPush anyway? [y/N] ", end="", flush=True)
            answer = tty.readline().strip().lower()
    except OSError:
        print(
            f"{message}\nNo terminal available to confirm; allowing push.",
            file=sys.stderr,
        )
        return True
    return answer == "y"


def main(argv: list[str] | None = None) -> int:
    """Entry point: parse pre-push stdin, gate real conflicts across every
    configured base branch (worst-wins), print the full D-05/D-06 grouped,
    capped, overlap-flagged commit summary, and return the exit code git
    uses to allow (0) or abort (non-zero) the push.

    argv is [remote_name, remote_url] per git's pre-push protocol -- accepted
    only to mirror the real `"$@"` invocation shape (D-01); not otherwise
    needed to decide the gate.
    """
    stdin_text = sys.stdin.read()
    updates = git_ops.parse_pre_push_stdin(stdin_text)

    toplevel = git_ops.repo_toplevel(".")
    if toplevel is None:
        return 0  # couldn't resolve a repo root -- never block

    cfg = config.load_config()
    resolved_toplevel = os.path.realpath(toplevel)
    matched_repo = next(
        (
            repo
            for repo in cfg.repos
            if os.path.realpath(repo.repo_path) == resolved_toplevel
        ),
        None,
    )
    if matched_repo is None:
        return 0  # not a watched repo -- never block a push bbwatch doesn't manage

    per_base: list[tuple[str, list[IncomingCommit]]] = []
    # D-07: fresh-at-push-time overlap set, unioned across every base (each
    # base's branch-unique paths depend on its own merge base) so D-06's flag
    # stays correct for every base's commit list, not just the last processed.
    overlap_paths: set[str] = set()
    gating = False
    real_conflict = False

    for local_ref, local_sha, _remote_ref, _remote_sha in updates:
        if git_ops.is_delete(local_ref, local_sha):
            continue
        for base in matched_repo.base_branches:
            verdict = base_verdict.assess_base(
                matched_repo.repo_path, base, local_sha, detailed=True
            )
            overlap_paths |= verdict.local_changes
            per_base.append((f"origin/{base}", verdict.incoming_commits))

            if not verdict.fetch_ok or verdict.merge_conflict is None:
                # Fetch failed after its retry (origin/<base> may be stale, so
                # a "clean" merge-tree proves nothing) or the dry run could
                # not run at all: gates the push, but is NOT a real conflict --
                # the prompt message below must not overclaim.
                gating = True
            elif verdict.merge_conflict:
                gating = True
                real_conflict = True

    for line in hook_summary.build_summary(per_base, overlap_paths=overlap_paths):
        print(line)

    if not gating:
        return 0

    # Routed through the same prompt either way so it's never an
    # unbypassable hard block (HOOK-03) -- only the message differs.
    message = (
        "Potential conflicts detected with the base branch."
        if real_conflict
        else "Could not determine whether the base branch conflicts "
        "(fetch/network issue) -- gating to be safe."
    )
    allowed = prompt_push_anyway(message)
    return 0 if allowed else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

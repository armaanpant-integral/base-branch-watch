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

from base_branch_watch.core import config, git_ops, hook_summary
from base_branch_watch.core.models import IncomingCommit


def prompt_push_anyway(message: str) -> bool:
    """D-08/D-09: fail-closed /dev/tty prompt with an env-var escape hatch.

    BBWATCH_SKIP_GATE=1 bypasses entirely, without ever opening /dev/tty.
    Otherwise reads the confirmation from /dev/tty -- never sys.stdin, which
    git repurposes for ref-update data during pre-push. If /dev/tty cannot
    be opened at all (no controlling terminal), fail closed: abort.
    """
    if os.environ.get("BBWATCH_SKIP_GATE") == "1":
        return True
    try:
        with open("/dev/tty") as tty:
            print(f"{message}\nPush anyway? [y/N] ", end="", flush=True)
            answer = tty.readline().strip().lower()
    except OSError:
        print(
            "No terminal available to confirm; aborting push. "
            "Re-run with BBWATCH_SKIP_GATE=1 to bypass.",
            file=sys.stderr,
        )
        return False
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
    # D-07: fresh-at-push-time overlap set. Unioned across every base rather
    # than kept as a single per-base last-write-wins value -- working_tree_paths
    # is base-independent (mirrors check_repo's own per-repo hoist in
    # git_ops.py), but branch_unique_paths depends on each base's own merge
    # base, so a repo with 2+ configured bases needs the union of all of them
    # for D-06's flag to stay correct for every base's commit list, not just
    # the last one processed (Rule 1 fix vs. a single-base-only reading of
    # 03-02-PLAN.md's overlap_paths=local_changes wiring).
    overlap_paths: set[str] = set()
    gating = False

    for local_ref, local_sha, _remote_ref, _remote_sha in updates:
        if git_ops.is_delete(local_ref, local_sha):
            continue
        for base in matched_repo.base_branches:
            git_ops.fetch(matched_repo.repo_path, base)
            origin_ref = f"origin/{base}"

            mb = git_ops.merge_base(matched_repo.repo_path, local_sha, origin_ref)
            local_changes = git_ops.working_tree_paths(matched_repo.repo_path) or set()
            if mb is not None:
                local_changes |= git_ops.branch_unique_paths(matched_repo.repo_path, mb) or set()
            overlap_paths |= local_changes

            commits = git_ops.incoming_commits(matched_repo.repo_path, mb, origin_ref) if mb else []
            per_base.append((origin_ref, commits))

            result = git_ops.merge_tree_dry_run(matched_repo.repo_path, local_sha, origin_ref)
            if result is None or result[0]:
                # result[0] True = a real conflict; None = undeterminable
                # (timeout, unresolvable ref, unrelated histories, ...).
                # Both gate the push -- fail-closed default abort (D-08),
                # routed through the same prompt so it's never an
                # unbypassable hard block (HOOK-03).
                gating = True

    for line in hook_summary.build_summary(per_base, overlap_paths=overlap_paths):
        print(line)

    if not gating:
        return 0

    allowed = prompt_push_anyway("Potential conflicts detected with the base branch.")
    return 0 if allowed else 1


if __name__ == "__main__":
    sys.exit(main())

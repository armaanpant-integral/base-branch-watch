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

from base_branch_watch.core import config, git_ops


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
    configured base branch (worst-wins), print a minimal drift summary, and
    return the exit code git uses to allow (0) or abort (non-zero) the push.

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

    summary_lines: list[str] = []
    gating = False

    for local_ref, local_sha, _remote_ref, _remote_sha in updates:
        if git_ops.is_delete(local_ref, local_sha):
            continue
        for base in matched_repo.base_branches:
            git_ops.fetch(matched_repo.repo_path, base)
            origin_ref = f"origin/{base}"

            behind, _ahead = git_ops.behind_ahead(matched_repo.repo_path, local_sha, origin_ref)
            behind_count = behind if behind >= 0 else 0
            summary_lines.append(f"{origin_ref}: {behind_count} incoming commit(s)")

            result = git_ops.merge_tree_dry_run(matched_repo.repo_path, local_sha, origin_ref)
            if result is None or result[0]:
                # result[0] True = a real conflict; None = undeterminable
                # (timeout, unresolvable ref, unrelated histories, ...).
                # Both gate the push -- fail-closed default abort (D-08),
                # routed through the same prompt so it's never an
                # unbypassable hard block (HOOK-03).
                gating = True

    for line in summary_lines:
        print(line)

    if not gating:
        return 0

    allowed = prompt_push_anyway("Potential conflicts detected with the base branch.")
    return 0 if allowed else 1


if __name__ == "__main__":
    sys.exit(main())

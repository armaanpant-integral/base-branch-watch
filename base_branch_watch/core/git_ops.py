"""All git subprocess invocation lives here. No rumps/AppKit import, ever.

Every subprocess.run call passes an explicit timeout= and an arg-list only,
no shell string interpolation — see PITFALLS.md Performance Traps + CLAUDE.md
What NOT to Use.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass

from base_branch_watch.core.models import BranchStatus, RepoConfig, RepoStatus, StatusKind

GIT = shutil.which("git") or "/usr/bin/git"


@dataclass
class FetchResult:
    ok: bool
    error: str | None = None


def _run_git(
    repo_path: str, args: list[str], timeout: int
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [GIT, "-C", repo_path, *args],
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def detect_default_branch(repo_path: str, timeout: int = 10) -> str | None:
    """Resolve origin's default branch via `ls-remote --symref origin HEAD`."""
    try:
        result = _run_git(repo_path, ["ls-remote", "--symref", "origin", "HEAD"], timeout)
    except (subprocess.TimeoutExpired, OSError):
        return None
    if result.returncode != 0:
        return None
    for line in result.stdout.splitlines():
        if line.startswith("ref:") and line.endswith("HEAD"):
            ref = line.split()[1]
            if ref.startswith("refs/heads/"):
                return ref[len("refs/heads/") :]
            return ref
    return None


def fetch(repo_path: str, base: str, timeout: int = 15) -> FetchResult:
    """`git fetch origin <base> --quiet`. Never raises on nonzero exit."""
    try:
        result = _run_git(repo_path, ["fetch", "origin", base, "--quiet"], timeout)
    except subprocess.TimeoutExpired:
        return FetchResult(ok=False, error=f"fetch timed out after {timeout}s")
    except OSError as exc:
        return FetchResult(ok=False, error=str(exc))
    if result.returncode != 0:
        return FetchResult(ok=False, error=result.stderr.strip() or "fetch failed")
    return FetchResult(ok=True)


def resolve_ref(repo_path: str, ref: str, timeout: int = 10) -> str | None:
    """`git rev-parse <ref>` — resolve any ref (branch, remote-tracking, etc.)
    to its current commit SHA. Never raises; returns None on failure/timeout.
    """
    try:
        result = _run_git(repo_path, ["rev-parse", ref], timeout)
    except (subprocess.TimeoutExpired, OSError):
        return None
    if result.returncode != 0:
        return None
    sha = result.stdout.strip()
    return sha or None


def current_branch(repo_path: str, timeout: int = 10) -> str | None:
    """`git rev-parse --abbrev-ref HEAD`."""
    try:
        result = _run_git(repo_path, ["rev-parse", "--abbrev-ref", "HEAD"], timeout)
    except (subprocess.TimeoutExpired, OSError):
        return None
    if result.returncode != 0:
        return None
    branch = result.stdout.strip()
    return branch or None


def behind_ahead(
    repo_path: str, left_ref: str, right_ref: str, timeout: int = 10
) -> tuple[int, int]:
    """(behind, ahead) of left_ref relative to right_ref via rev-list --left-right --count.

    Never raises — mirrors every sibling function in this module. Returns
    (-1, -1) as a sentinel if the underlying git invocation itself times out
    or errors (OSError/TimeoutExpired) — distinct from a genuine (0, 0) "no
    divergence" result, so callers like check_repo can tell "confirmed no
    divergence" apart from "couldn't determine" (WR-04). A nonzero git exit
    (e.g. an unresolvable ref) still collapses to (0, 0), unchanged.
    """
    try:
        result = _run_git(
            repo_path,
            ["rev-list", "--left-right", "--count", f"{left_ref}...{right_ref}"],
            timeout,
        )
    except (subprocess.TimeoutExpired, OSError):
        return (-1, -1)
    if result.returncode != 0:
        return (0, 0)
    parts = result.stdout.strip().split()
    if len(parts) != 2:
        return (0, 0)
    ahead_str, behind_str = parts
    ahead = int(ahead_str) if ahead_str.isdigit() else 0
    behind = int(behind_str) if behind_str.isdigit() else 0
    return (behind, ahead)


def merge_base(repo_path: str, left: str, right: str, timeout: int = 10) -> str | None:
    """`git merge-base left right`. Never raises. Returns None on failure —
    exit 128 (unresolvable ref, e.g. empty repo/no commits) and exit 1
    (no common ancestor, e.g. rewritten/unrelated history) both collapse to
    None; only exit 0 with a SHA on stdout counts as success.
    """
    try:
        result = _run_git(repo_path, ["merge-base", left, right], timeout)
    except (subprocess.TimeoutExpired, OSError):
        return None
    if result.returncode != 0:
        return None
    sha = result.stdout.strip()
    return sha or None


def _parse_name_status_z(raw: str) -> set[str]:
    """Parse `git diff --name-status -z` output into a flat path set.

    Rename/copy (`R`/`C`) records carry TWO path fields (old, new for `git
    diff --name-status -z`); both are added to the set so a local edit to a
    file's pre-rename name still overlap-matches an incoming rename of that
    same file (see 02-RESEARCH.md Pattern 2 / Pitfall 1).
    """
    tokens = raw.split("\0")
    paths: set[str] = set()
    i = 0
    while i < len(tokens):
        status = tokens[i]
        if not status:
            i += 1
            continue
        if status[0] in ("R", "C"):
            paths.add(tokens[i + 1])
            paths.add(tokens[i + 2])
            i += 3
        else:
            paths.add(tokens[i + 1])
            i += 2
    return paths


def working_tree_paths(repo_path: str, timeout: int = 10) -> set[str] | None:
    """Union of tracked-and-changed paths (staged + unstaged vs HEAD) and
    untracked non-ignored paths. Base-independent (D-01 working-tree half +
    D-02 untracked) — compute once per repo, not per base.

    Returns None only on subprocess timeout/OSError for either half. A
    nonzero returncode on the diff half (e.g. unborn HEAD) collapses to an
    empty set for that half, not None — mirrors behind_ahead's distinction
    between "couldn't determine" and "confirmed empty."
    """
    try:
        diff_result = _run_git(repo_path, ["diff", "--name-status", "-z", "HEAD"], timeout)
    except (subprocess.TimeoutExpired, OSError):
        return None
    changed = _parse_name_status_z(diff_result.stdout) if diff_result.returncode == 0 else set()

    try:
        untracked_result = _run_git(
            repo_path, ["ls-files", "--others", "--exclude-standard", "-z"], timeout
        )
    except (subprocess.TimeoutExpired, OSError):
        return None
    if untracked_result.returncode != 0:
        untracked: set[str] = set()
    else:
        untracked = {p for p in untracked_result.stdout.split("\0") if p}

    return changed | untracked


def branch_unique_paths(repo_path: str, mb: str, timeout: int = 10) -> set[str] | None:
    """Paths changed between `mb` (merge-base) and HEAD — D-01's branch-unique
    half, base-dependent. None on subprocess failure or nonzero returncode."""
    try:
        result = _run_git(repo_path, ["diff", "--name-status", "-z", mb, "HEAD"], timeout)
    except (subprocess.TimeoutExpired, OSError):
        return None
    if result.returncode != 0:
        return None
    return _parse_name_status_z(result.stdout)


def incoming_changed_paths(
    repo_path: str, mb: str, base: str, timeout: int = 10
) -> set[str] | None:
    """Paths changed between `mb` (merge-base) and `origin/<base>` — D-03's
    incoming window. Uses `--name-status -z` in place of D-03's literal
    `--name-only` (02-RESEARCH.md Pattern 2): same ref range, same intent,
    rename/unicode-correct. Never a merge command (D-05). None on failure."""
    try:
        result = _run_git(
            repo_path, ["diff", "--name-status", "-z", mb, f"origin/{base}"], timeout
        )
    except (subprocess.TimeoutExpired, OSError):
        return None
    if result.returncode != 0:
        return None
    return _parse_name_status_z(result.stdout)


def unpushed_count(repo_path: str, timeout: int = 10) -> int:
    """Count of commits on HEAD not yet on its own upstream (`@{u}`).

    `rev-list --count @{u}..HEAD`. Returns 0 when `@{u}` can't be resolved
    (no upstream configured for the current branch) - never raises.
    """
    try:
        result = _run_git(repo_path, ["rev-list", "--count", "@{u}..HEAD"], timeout)
    except (subprocess.TimeoutExpired, OSError):
        return 0
    if result.returncode != 0:
        return 0
    out = result.stdout.strip()
    return int(out) if out.isdigit() else 0


def check_repo(repo: RepoConfig) -> RepoStatus:
    """Full status: every configured base branch + repo-level unpushed count.

    `unpushed` is computed once per repo (not per base) via `unpushed_count`.
    Each base is fetched independently with one retry on failure (Pitfall
    8) - a base whose fetch still fails after the retry gets a distinct
    CHECK_FAILED BranchStatus (never a bogus behind count), and checking
    continues for the repo's other bases rather than failing the whole repo.
    Never raises.
    """
    name = os.path.basename(repo.repo_path.rstrip("/"))

    if not os.path.isdir(os.path.join(repo.repo_path, ".git")):
        return RepoStatus(
            repo_path=repo.repo_path,
            name=name,
            current_branch=None,
            unpushed=0,
            branch_statuses=[],
            failure_reason="repo folder not found — was it moved or deleted?",
        )

    if not repo.base_branches:
        return RepoStatus(
            repo_path=repo.repo_path,
            name=name,
            current_branch=None,
            unpushed=0,
            branch_statuses=[],
            failure_reason="no base branches configured",
        )

    branch = current_branch(repo.repo_path)
    unpushed = unpushed_count(repo.repo_path)
    # Base-independent half of D-01/D-02's local-change set (working-tree
    # diff + untracked files) — computed once per repo, mirroring how
    # `unpushed` is hoisted above the per-base loop (02-PATTERNS.md).
    local_wt = working_tree_paths(repo.repo_path)

    branch_statuses: list[BranchStatus] = []
    for base in repo.base_branches:
        fetch_result = _fetch_with_retry(repo.repo_path, base)

        if not fetch_result.ok:
            branch_statuses.append(
                BranchStatus(
                    base=base,
                    behind=0,
                    ahead_of_base=0,
                    kind=StatusKind.CHECK_FAILED,
                    reason="fetch failed — check network/SSH access",
                )
            )
            continue

        # behind_ahead never raises (WR-02); it signals "couldn't determine"
        # (fetch succeeded, but the local rev-list comparison itself timed
        # out/errored) via the (-1, -1) sentinel, distinct from a genuine
        # (0, 0) "no divergence" result.
        behind, ahead = behind_ahead(repo.repo_path, "HEAD", f"origin/{base}")
        if behind == -1 and ahead == -1:
            branch_statuses.append(
                BranchStatus(
                    base=base,
                    behind=0,
                    ahead_of_base=0,
                    kind=StatusKind.CHECK_FAILED,
                    reason="status check failed — local git error",
                )
            )
            continue

        if behind > 0:
            # Conflict-risk overlap check (CONFLICT-01) — only worth computing
            # when there's an incoming range to compare against at all.
            mb = merge_base(repo.repo_path, "HEAD", f"origin/{base}")
            incoming = (
                incoming_changed_paths(repo.repo_path, mb, base) if mb is not None else None
            )
            branch_unique = (
                branch_unique_paths(repo.repo_path, mb) if mb is not None else None
            )
            if (
                mb is None
                or local_wt is None
                or incoming is None
                or branch_unique is None
            ):
                branch_statuses.append(
                    BranchStatus(
                        base=base,
                        behind=0,
                        ahead_of_base=0,
                        kind=StatusKind.CHECK_FAILED,
                        reason="conflict check failed — local git error",
                    )
                )
                continue
            local = local_wt | branch_unique
            overlap = incoming & local
            if overlap:
                branch_statuses.append(
                    BranchStatus(
                        base=base,
                        behind=behind,
                        ahead_of_base=ahead,
                        kind=StatusKind.CONFLICT_RISK,
                        conflict_paths=sorted(overlap),
                    )
                )
                continue

        if behind > 0 and ahead > 0:
            kind = StatusKind.DIVERGED
        elif behind > 0:
            kind = StatusKind.BEHIND
        else:
            kind = StatusKind.UP_TO_DATE
        branch_statuses.append(
            BranchStatus(base=base, behind=behind, ahead_of_base=ahead, kind=kind)
        )

    return RepoStatus(
        repo_path=repo.repo_path,
        name=name,
        current_branch=branch,
        unpushed=unpushed,
        branch_statuses=branch_statuses,
        failure_reason=None,
    )


def _fetch_with_retry(repo_path: str, base: str) -> FetchResult:
    """`fetch` once, retrying a single time on failure (Pitfall 8 - distinguish
    a transient network blip from a genuinely unreachable remote)."""
    try:
        result = fetch(repo_path, base)
    except (subprocess.TimeoutExpired, OSError) as exc:
        result = FetchResult(ok=False, error=str(exc))
    if result.ok:
        return result
    try:
        return fetch(repo_path, base)
    except (subprocess.TimeoutExpired, OSError) as exc:
        return FetchResult(ok=False, error=str(exc))

"""All git subprocess invocation lives here. No rumps/AppKit import, ever.

Every subprocess.run call passes an explicit timeout= and an arg-list only,
no shell string interpolation — see PITFALLS.md Performance Traps + CLAUDE.md
What NOT to Use.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from dataclasses import dataclass

from base_branch_watch.core import log
from base_branch_watch.core.models import (
    IncomingCommit,
)

GIT = shutil.which("git") or "/usr/bin/git"

_ERROR_LINE_NOISE_PREFIX = re.compile(
    r"^(?:(?:fatal|error): )?(?:unable to access '[^']*': )?", re.IGNORECASE
)

FETCH_ERROR_EXCERPT_CAP = 60
FETCH_ERROR_LOG_CAP = 1000

# Ordered: first matching rule wins, so specific causes sit above generic ones
# (git's trailing "Could not read from remote repository" fits several causes
# and is deliberately NOT a rule of its own).
_FETCH_ERROR_RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("timeout", ("fetch timed out",)),
    ("base branch not found on origin", ("couldn't find remote ref",)),
    (
        "auth or access denied",
        (
            "permission denied",
            "authentication failed",
            "repository not found",
            "host key verification failed",
            "could not read username",
            "terminal prompts disabled",
            "returned error: 401",
            "returned error: 403",
        ),
    ),
    ("tls certificate problem", ("ssl certificate problem",)),
    ("remote server error", ("returned error: 5",)),
    (
        "network unreachable",
        (
            "failed to connect to",
            "couldn't connect to server",
            "ssl_error_syscall",
            "could not resolve host",
            "network is unreachable",
            "no route to host",
            "connection refused",
            "connection timed out",
            "operation timed out",
            "connection reset",
            "connection closed",
            "temporary failure in name resolution",
        ),
    ),
    (
        "ref locked",
        (
            "cannot lock ref",
            "another git process seems to be running",
            ".lock': file exists",
        ),
    ),
    ("origin is not a git repository", ("does not appear to be a git repository",)),
    # EDEADLK from open(): macOS refuses to download an iCloud-evicted (dataless)
    # file for a background process such as the LaunchAgent.
    ("iCloud-evicted .git file", ("resource deadlock avoided",)),
)

SF_DATALESS = 0x40000000
"""macOS st_flags bit set on a file whose contents iCloud has evicted. Not
exposed by the stat module."""

ZERO_SHA = "0" * 40
"""All-zeros SHA git uses on the pre-push stdin protocol to mean "no commit
on this side" -- a deleted ref's local side, or a brand-new ref's remote
side (03-RESEARCH.md Pattern 1)."""


@dataclass
class FetchResult:
    ok: bool
    error: str | None = None


@dataclass
class LocalBranch:
    name: str
    sha: str
    upstream_remote: str | None
    upstream_ref: str | None
    """The branch's ref on its upstream remote, e.g. `refs/heads/feat/x`."""
    worktree_path: str | None
    """Set when the branch is checked out in a worktree (the main one or a linked one)."""


_LOCAL_BRANCH_FORMAT = (
    "%(refname:short)%00%(objectname)%00%(upstream:remotename)%00"
    "%(upstream:remoteref)%00%(worktreepath)"
)


def _run_git(
    repo_path: str, args: list[str], timeout: int, *, text: bool = True
) -> subprocess.CompletedProcess:
    """The one subprocess template. Raises on timeout/OSError; fetch() needs
    that distinction for its error text, everything else uses _try_git."""
    return subprocess.run(
        [GIT, "-C", repo_path, *args],
        capture_output=True,
        text=text,
        timeout=timeout,
    )


def _try_git(
    repo_path: str, args: list[str], timeout: int, *, text: bool = True
) -> subprocess.CompletedProcess | None:
    """_run_git that never raises: None means "git could not run or timed out"
    (the module-wide "couldn't determine" case), distinct from a nonzero exit."""
    try:
        return _run_git(repo_path, args, timeout, text=text)
    except (subprocess.TimeoutExpired, OSError):
        return None


def detect_default_branch(repo_path: str, timeout: int = 10) -> str | None:
    """Resolve origin's default branch via `ls-remote --symref origin HEAD`."""
    result = _try_git(repo_path, ["ls-remote", "--symref", "origin", "HEAD"], timeout)
    if result is None:
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


def remote_heads(repo_path: str, timeout: int = 15) -> dict[str, str] | None:
    """`git ls-remote --heads origin` as {branch name: sha}. Read-only, never raises.

    None means "could not tell" (git failed, timed out, or origin listed no branches at
    all), never an empty answer, so a caller cannot mistake an unreachable or empty
    origin for "every remote branch is gone".
    """
    result = _try_git(repo_path, ["ls-remote", "--heads", "origin"], timeout)
    if result is None or result.returncode != 0:
        return None
    heads: dict[str, str] = {}
    for line in result.stdout.splitlines():
        sha, _, ref = line.partition("\t")
        heads[ref.removeprefix("refs/heads/")] = sha
    return heads or None


def local_branches(repo_path: str, timeout: int = 10) -> list[LocalBranch] | None:
    """Every local branch with its upstream and checked-out worktree. Read-only, never raises.

    None means "could not tell" (git failed, timed out or printed something unparseable),
    never an empty list, so a failure cannot be mistaken for "no branches, nothing stale".
    """
    result = _try_git(
        repo_path, ["for-each-ref", f"--format={_LOCAL_BRANCH_FORMAT}", "refs/heads"], timeout
    )
    if result is None or result.returncode != 0:
        return None
    branches = []
    for line in result.stdout.splitlines():
        fields = line.split("\x00")
        if len(fields) != 5:
            return None
        name, sha, remote, remote_ref, worktree = fields
        branches.append(
            LocalBranch(
                name=name,
                sha=sha,
                upstream_remote=remote or None,
                upstream_ref=remote_ref or None,
                worktree_path=worktree or None,
            )
        )
    return branches


def is_ancestor(repo_path: str, ancestor: str, descendant: str, timeout: int = 10) -> bool | None:
    """True when every commit of `ancestor` is reachable from `descendant`. Never raises.

    git exits 1 for "not an ancestor" and 128 for a bad or missing ref; only the first is
    a real answer. None means "could not tell", never False.
    """
    result = _try_git(repo_path, ["merge-base", "--is-ancestor", ancestor, descendant], timeout)
    if result is None or result.returncode not in (0, 1):
        return None
    return result.returncode == 0


def _remove_dataless_fetch_head(repo_path: str) -> None:
    """Delete `.git/FETCH_HEAD` if iCloud evicted it. Never raises.

    A background process cannot materialize a dataless file (open() fails with
    EDEADLK), so the fetch would fail forever. FETCH_HEAD is a throwaway file
    git rewrites on every fetch, so removing the placeholder is safe.
    """
    fetch_head_path = os.path.join(repo_path, ".git", "FETCH_HEAD")
    try:
        if not os.lstat(fetch_head_path).st_flags & SF_DATALESS:
            return
        os.unlink(fetch_head_path)
    except (OSError, AttributeError):
        return
    try:
        log.append(f"[INFO] removed iCloud-evicted FETCH_HEAD in {repo_path}")
    except OSError:
        pass


def fetch(repo_path: str, base: str, timeout: int = 15) -> FetchResult:
    """`git fetch origin <base> --quiet`. Never raises on nonzero exit."""
    _remove_dataless_fetch_head(repo_path)
    try:
        result = _run_git(repo_path, ["fetch", "--quiet", "origin", "--", base], timeout)
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
    result = _try_git(repo_path, ["rev-parse", ref], timeout)
    if result is None:
        return None
    if result.returncode != 0:
        return None
    sha = result.stdout.strip()
    return sha or None


def current_branch(repo_path: str, timeout: int = 10) -> str | None:
    """`git rev-parse --abbrev-ref HEAD`."""
    result = _try_git(repo_path, ["rev-parse", "--abbrev-ref", "HEAD"], timeout)
    if result is None:
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
    result = _try_git(
        repo_path,
        ["rev-list", "--left-right", "--count", f"{left_ref}...{right_ref}"],
        timeout,
    )
    if result is None:
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
    result = _try_git(repo_path, ["merge-base", left, right], timeout)
    if result is None:
        return None
    if result.returncode != 0:
        return None
    sha = result.stdout.strip()
    return sha or None


def merge_tree_dry_run(
    repo_path: str, branch1: str, branch2: str, timeout: int = 20
) -> tuple[bool, list[str]] | None:
    """`git merge-tree --write-tree --name-only -z -- branch1 branch2` dry run.

    Authoritative, non-mutating conflict check (git >= 2.38) -- writes an
    unreferenced loose object into .git/objects and does NOT touch the
    working tree, index, or HEAD (03-RESEARCH.md Pitfall 6). Runs with
    text=False so the NUL-separated stdout can be split exactly as bytes.

    branch1/branch2 sit after a mandatory "--" separator: they are
    config-controlled base-branch names flowing into a git argument list,
    and a value like "--stdin" would otherwise be interpreted as a real
    merge-tree flag (extends fetch()'s CR-01 guard -- 03-RESEARCH.md Pitfall
    2, empirically reproduced as a live silent-bypass PoC this session).

    Exit-status semantics (03-RESEARCH.md Pattern 2 / Pitfall 3b):
    - returncode == 0                        -> clean merge -> (False, [])
    - returncode == 1 AND non-empty stdout    -> real conflict -> (True, paths)
    - returncode == 1 AND EMPTY stdout        -> an unresolvable ref (e.g. a
      typo'd base, or origin/<base> not yet fetched) -- NOT a conflict, and
      must never be parsed as one.
    - any other returncode                    -> a genuine error.
    The last two cases both collapse to None, the same "couldn't determine"
    sentinel used everywhere else in this file -- never confuse either with
    a real conflict or a clean result.
    """
    result = _try_git(
        repo_path,
        ["merge-tree", "--write-tree", "--name-only", "-z", "--", branch1, branch2],
        timeout,
        text=False,
    )
    if result is None:
        return None
    if result.returncode == 0:
        return (False, [])
    if result.returncode == 1 and result.stdout:
        parts = result.stdout.split(b"\x00")
        conflicted: list[str] = []
        for part in parts[1:]:  # parts[0] is the written tree OID
            if part == b"":
                break  # first empty part terminates the conflicted-path list
            conflicted.append(part.decode("utf-8", "surrogateescape"))
        return (True, conflicted)
    return None  # any other code, OR returncode==1 with empty stdout (unresolvable ref)


def hooks_path(repo_path: str, timeout: int = 10) -> str | None:
    """`git rev-parse --git-path hooks`, resolved to an ABSOLUTE path.

    Honors core.hooksPath overrides -- never hardcode ".git/hooks"
    (03-RESEARCH.md Pitfall 1). The raw output is repo-relative in both the
    default case AND a relative core.hooksPath override; only an absolute
    override value yields an absolute result directly. Always join a
    relative result onto repo_path.
    """
    result = _try_git(repo_path, ["rev-parse", "--git-path", "hooks"], timeout)
    if result is None:
        return None
    if result.returncode != 0:
        return None
    raw_path = result.stdout.strip()
    if not raw_path:
        return None
    if os.path.isabs(raw_path):
        return raw_path
    return os.path.normpath(os.path.join(repo_path, raw_path))


def repo_toplevel(repo_path: str = ".", timeout: int = 10) -> str | None:
    """`git rev-parse --show-toplevel`. Never raises; None on failure."""
    result = _try_git(repo_path, ["rev-parse", "--show-toplevel"], timeout)
    if result is None:
        return None
    if result.returncode != 0:
        return None
    toplevel = result.stdout.strip()
    return toplevel or None


def parse_pre_push_stdin(stdin_text: str) -> list[tuple[str, str, str, str]]:
    """Parse git's pre-push ref-update lines (PURE -- no subprocess, never raises).

    One line per ref being pushed:
        <local-ref> SP <local-sha1> SP <remote-ref> SP <remote-sha1> LF
    (03-RESEARCH.md Pattern 1, empirically reproduced against a real
    `git push` this session -- normal push, delete push, multi-ref push).
    Lines that don't split into exactly 4 whitespace-separated fields are
    skipped defensively (blank lines, malformed input).
    """
    updates: list[tuple[str, str, str, str]] = []
    for line in stdin_text.splitlines():
        parts = line.split()
        if len(parts) != 4:
            continue
        updates.append((parts[0], parts[1], parts[2], parts[3]))
    return updates


def is_delete(local_ref: str, local_sha: str) -> bool:
    """A ref update is a delete when its local side is all-zeros -- nothing
    being pushed for that ref, so there is no local commit to conflict-check.
    """
    return local_sha == ZERO_SHA


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
    diff_result = _try_git(repo_path, ["diff", "--name-status", "-z", "HEAD"], timeout)
    if diff_result is None:
        return None
    changed = _parse_name_status_z(diff_result.stdout) if diff_result.returncode == 0 else set()

    untracked_result = _try_git(
        repo_path, ["ls-files", "--others", "--exclude-standard", "-z"], timeout
    )
    if untracked_result is None:
        return None
    if untracked_result.returncode != 0:
        untracked: set[str] = set()
    else:
        untracked = {p for p in untracked_result.stdout.split("\0") if p}

    return changed | untracked


def branch_unique_paths(
    repo_path: str, mb: str, right_ref: str = "HEAD", timeout: int = 10
) -> set[str] | None:
    """Paths changed between `mb` (merge-base) and `right_ref` — D-01's
    branch-unique half, base-dependent. `right_ref` defaults to `HEAD` for
    the poller's own use (check_repo), but hook.py must pass the actual
    pushed `local_sha` from git's pre-push protocol — the checked-out HEAD
    is not necessarily the ref being pushed (multi-ref pushes, pushing a
    non-checked-out local branch). None on subprocess failure or nonzero
    returncode."""
    result = _try_git(repo_path, ["diff", "--name-status", "-z", mb, right_ref], timeout)
    if result is None:
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
    result = _try_git(repo_path, ["diff", "--name-status", "-z", mb, f"origin/{base}"], timeout)
    if result is None:
        return None
    if result.returncode != 0:
        return None
    return _parse_name_status_z(result.stdout)


# incoming_commits' `git log` record layout: the format string and the parser
# share these so they cannot drift apart.
_RECORD_SEP = "\x1e"
_FIELD_SEP = "\x1f"


def incoming_commits(
    repo_path: str, mb: str, base_ref: str, timeout: int = 15
) -> list[IncomingCommit]:
    """D-05's per-base commit list: `git log --no-merges mb..base_ref`,
    newest-first, each with its own changed-path set for D-06's per-commit
    overlap flag. One git call for the whole range (header + `--name-only -z`
    paths per commit); `--no-renames` keeps both sides of a rename. `mb` is always a merge-base SHA
    (never dash-prefixed) and `base_ref` is always caller-constructed as
    `origin/<base>` (never dash-prefixed either) -- the `mb..base_ref` range
    positional can never be mistaken for a flag, mirroring `merge_base`'s
    existing bare-positional precedent in this same file. Never raises; [] on
    any failure.

    Record layout: \x1e <hash> \x1f <author> \x1f <subject> \0 \n <path> \0 <path> \0 ...
    """
    result = _try_git(
        repo_path,
        [
            "log",
            "--no-merges",
            "--no-renames",
            "--name-only",
            "-z",
            "--format=%x1e%h%x1f%an%x1f%s",
            f"{mb}..{base_ref}",
        ],
        timeout,
    )
    if result is None or result.returncode != 0:
        return []
    commits: list[IncomingCommit] = []
    for record in result.stdout.split(_RECORD_SEP):
        header, _, path_block = record.partition("\0")
        parts = header.split(_FIELD_SEP, 2)
        if len(parts) != 3:
            continue
        short_hash, author, subject = parts
        changed_paths = {p for p in path_block.removeprefix("\n").split("\0") if p}
        commits.append(
            IncomingCommit(
                short_hash=short_hash,
                author=author,
                subject=subject,
                changed_paths=changed_paths,
            )
        )
    return commits


def unpushed_count(repo_path: str, timeout: int = 10) -> int:
    """Count of commits on HEAD not yet on its own upstream (`@{u}`).

    `rev-list --count @{u}..HEAD`. Returns 0 when `@{u}` can't be resolved
    (no upstream configured for the current branch) and also when git itself
    fails or times out - callers cannot tell the two apart and do not need to:
    both mean "nothing to report as unpushed". Never raises.
    """
    result = _try_git(repo_path, ["rev-list", "--count", "@{u}..HEAD"], timeout)
    if result is None:
        return 0
    if result.returncode != 0:
        return 0
    out = result.stdout.strip()
    return int(out) if out.isdigit() else 0


def classify_fetch_error(fetch_error_text: str | None) -> str:
    """Short human label for why a fetch failed, from git's stderr / our timeout text.

    Never raises; unrecognised or empty text yields "other" so a new git error
    string degrades to a generic label rather than a wrong specific one.
    """
    lowered_error_text = (fetch_error_text or "").lower()
    for cause_label, needles in _FETCH_ERROR_RULES:
        if any(needle in lowered_error_text for needle in needles):
            return cause_label
    return "other"


def _first_error_line(fetch_error_text: str | None) -> str:
    for raw_line in (fetch_error_text or "").splitlines():
        stripped_line = " ".join(raw_line.split())
        if stripped_line:
            diagnostic_text = _ERROR_LINE_NOISE_PREFIX.sub("", stripped_line, count=1)
            return (diagnostic_text or stripped_line)[:FETCH_ERROR_EXCERPT_CAP]
    return ""


def fetch_failure_reason(fetch_error_text: str | None) -> str:
    """User-facing `BranchStatus.reason` for a failed fetch: cause label + first stderr line."""
    cause_label = classify_fetch_error(fetch_error_text)
    error_excerpt = _first_error_line(fetch_error_text)
    if not error_excerpt:
        return f"fetch failed ({cause_label})"
    return f"fetch failed ({cause_label}): {error_excerpt}"


def log_fetch_failure(repo_path: str, base: str, fetch_error_text: str | None) -> None:
    """Append the full (single-line, capped) fetch error to the app log. Never raises."""
    collapsed_error_text = " | ".join(
        " ".join(raw_line.split())
        for raw_line in (fetch_error_text or "").splitlines()
        if raw_line.strip()
    )
    cause_label = classify_fetch_error(fetch_error_text)
    try:
        log.append(
            f"[FAIL] fetch failed for {repo_path} (base {base}, cause {cause_label}): "
            f"{collapsed_error_text[:FETCH_ERROR_LOG_CAP] or 'no error output'}"
        )
    except OSError:
        pass


def fetch_with_retry(repo_path: str, base: str) -> FetchResult:
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

"""Bounded ThreadPoolExecutor fan-out for per-repo status checks.

No rumps/AppKit import here, ever — stays pure and reusable by any future
non-app porcelain (ARCHITECTURE.md's plumbing/porcelain split, Suggested
Build Order step 3). Retry-once-on-fetch-failure already lives in
core.git_ops.check_repo (Plan 03); this module only orchestrates parallelism
and per-repo failure isolation, it does not retry anything itself.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed

from base_branch_watch.core import git_ops
from base_branch_watch.core.models import RepoConfig, RepoStatus


def check_all(repos: list[RepoConfig], max_workers: int = 8) -> list[RepoStatus]:
    """Fan out core.git_ops.check_repo across repos via a bounded thread pool.

    Worker count is capped at min(max_workers, len(repos)) — never exceeds the
    repo count, and the empty-list guard avoids ever constructing a
    zero-worker pool. A per-repo exception is isolated to a CHECK_FAILED
    RepoStatus (via RepoStatus.failed) rather than propagating and killing
    the whole batch (Pitfall 8 / T-4-03).
    """
    if not repos:
        return []

    results: list[RepoStatus] = []
    with ThreadPoolExecutor(max_workers=min(max_workers, len(repos))) as pool:
        futures = {pool.submit(git_ops.check_repo, repo): repo for repo in repos}
        for future in as_completed(futures):
            repo = futures[future]
            try:
                results.append(future.result())
            except Exception as exc:  # noqa: BLE001 - isolate any per-repo failure
                results.append(RepoStatus.failed(repo, str(exc)))
    return results

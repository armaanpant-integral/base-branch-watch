"""Bounded ThreadPoolExecutor fan-out for per-repo status checks.

No rumps/AppKit import here, ever — stays pure and reusable by any future
non-app porcelain (ARCHITECTURE.md's plumbing/porcelain split, Suggested
Build Order step 3). Retry-once-on-fetch-failure already lives in
core.git_ops.check_repo (Plan 03); this module only orchestrates parallelism
and per-repo failure isolation, it does not retry anything itself.

D-12 (04-pr-status Plan 01): PR status is fetched via core.pr_status.check_pr
in the SAME pool as the git-status check, one extra `gh` call per repo, no
second concurrency mechanism. A gh failure isolates to that repo's PrStatus
(D-11) exactly like a git failure isolates to that repo's RepoStatus.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed

from base_branch_watch.core import git_ops, pr_status
from base_branch_watch.core.models import PrStatus, RepoConfig, RepoStatus


def _check_one(repo: RepoConfig) -> tuple[RepoStatus, PrStatus]:
    return git_ops.check_repo(repo), pr_status.check_pr(repo.repo_path)


def check_all(
    repos: list[RepoConfig], max_workers: int = 8
) -> tuple[list[RepoStatus], dict[str, PrStatus]]:
    """Fan out per-repo git-status + PR-status checks via a bounded thread pool.

    Worker count is capped at min(max_workers, len(repos)) — never exceeds the
    repo count, and the empty-list guard avoids ever constructing a
    zero-worker pool. A per-repo exception is isolated to a CHECK_FAILED
    RepoStatus (via RepoStatus.failed) paired with a CHECK_FAILED PrStatus
    (via PrStatus.failed) rather than propagating and killing the whole batch
    (Pitfall 8 / T-4-03 / D-11).
    """
    if not repos:
        return [], {}

    results: list[RepoStatus] = []
    pr_statuses: dict[str, PrStatus] = {}
    with ThreadPoolExecutor(max_workers=min(max_workers, len(repos))) as pool:
        futures = {pool.submit(_check_one, repo): repo for repo in repos}
        for future in as_completed(futures):
            repo = futures[future]
            try:
                status, pr = future.result()
            except Exception as exc:  # noqa: BLE001 - isolate any per-repo failure
                status = RepoStatus.failed(repo, str(exc))
                pr = PrStatus.failed(str(exc))
            results.append(status)
            pr_statuses[repo.repo_path] = pr
    return results, pr_statuses

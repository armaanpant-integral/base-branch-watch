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


def _check_one(
    repo: RepoConfig, pr_repo_paths: set[str] | None
) -> tuple[RepoStatus, PrStatus | None]:
    status = git_ops.check_repo(repo)
    # D-13's ~2min floor gate (Plan 02): when pr_repo_paths is a set, only a
    # repo whose repo_path is IN it gets a gh call this cycle -- the floor's
    # scheduling decision lives in app/menubar.py, this is purely mechanical.
    # None (default) preserves Plan 01 behavior: every repo gets a check_pr
    # call, no gate.
    if pr_repo_paths is not None and repo.repo_path not in pr_repo_paths:
        return status, None
    return status, pr_status.check_pr(repo.repo_path)


def check_all(
    repos: list[RepoConfig],
    max_workers: int = 8,
    pr_repo_paths: set[str] | None = None,
) -> tuple[list[RepoStatus], dict[str, PrStatus]]:
    """Fan out per-repo git-status + PR-status checks via a bounded thread pool.

    Worker count is capped at min(max_workers, len(repos)) — never exceeds the
    repo count, and the empty-list guard avoids ever constructing a
    zero-worker pool. A per-repo exception is isolated to a CHECK_FAILED
    RepoStatus (via RepoStatus.failed) paired with a CHECK_FAILED PrStatus
    (via PrStatus.failed) rather than propagating and killing the whole batch
    (Pitfall 8 / T-4-03 / D-11).

    pr_repo_paths (Plan 02, D-13): when a set, only repos whose repo_path is
    a member get a check_pr call this cycle; repos outside the set are
    omitted from the returned pr_statuses dict entirely (their RepoStatus
    git check still runs normally). None (default) keeps every repo getting
    a check_pr call, unchanged from Plan 01.
    """
    if not repos:
        return [], {}

    results: list[RepoStatus] = []
    pr_statuses: dict[str, PrStatus] = {}
    with ThreadPoolExecutor(max_workers=min(max_workers, len(repos))) as pool:
        futures = {pool.submit(_check_one, repo, pr_repo_paths): repo for repo in repos}
        for future in as_completed(futures):
            repo = futures[future]
            try:
                status, pr = future.result()
            except Exception as exc:  # noqa: BLE001 - isolate any per-repo failure
                status = RepoStatus.failed(repo, str(exc))
                pr = PrStatus.failed(str(exc))
            results.append(status)
            if pr is not None:
                pr_statuses[repo.repo_path] = pr
    return results, pr_statuses

"""Thin rumps.App glue: Timer, recursive MenuItemSpec render, single inline
notification, real Open Log action.

Keeps app/ free of any git/JSON logic beyond calling core.* (ARCHITECTURE.md
Anti-Pattern 1). The only subprocess uses permitted here are the osascript
notification and the `open -e` Open Log action — all git/config/log state
goes through core.*.
"""

from __future__ import annotations

import datetime
import subprocess

import rumps

from base_branch_watch.app import menu_builder
from base_branch_watch.core import config, git_ops, log
from base_branch_watch.core.models import MenuItemSpec, RepoStatus, StatusKind

ADD_REPO_TITLE = "Add Repo…"
REFRESH_TITLE = "Refresh Now"
OPEN_LOG_TITLE = "Open Log"
QUIT_TITLE = "Quit"


def _notify(title: str, subtitle: str, body: str) -> None:
    """Fire one osascript display-notification banner. Quote-escaped per prototype parity."""
    body = body.replace('"', "'")
    title = title.replace('"', "'")
    subtitle = subtitle.replace('"', "'")
    script = (
        f'display notification "{body}" with title "{title}" '
        f'subtitle "{subtitle}" sound name "Glass"'
    )
    subprocess.run(["osascript", "-e", script], timeout=10)


class BaseBranchWatchApp(rumps.App):
    def __init__(self):
        super().__init__("…", quit_button=None)
        self.cfg = config.load_config()
        self.statuses: dict[str, RepoStatus] = {}
        self._repo_items: dict[str, rumps.MenuItem] = {}
        # rumps.MenuItem's dict key in a Menu is fixed to its *title at insertion
        # time*, even after .title mutates later (see rumps.MenuItem docstring) —
        # track it separately so deletion can find the item by its original key.
        self._repo_item_keys: dict[str, str] = {}
        self._empty_item: rumps.MenuItem | None = None
        self._checking = False

        self._build_menu_shell()

        self.timer = rumps.Timer(self.check_all, self.cfg.poll_interval_seconds)
        self.timer.start()
        self.check_all(None)

    # -- menu construction -------------------------------------------------

    def _build_menu_shell(self) -> None:
        """Build the menu once: repo rows (mutated in place thereafter) + static items."""
        self._render(list(self.statuses.values()))
        self.menu.add(rumps.separator)
        self.menu.add(rumps.MenuItem(ADD_REPO_TITLE, callback=self._add_repo))
        self.menu.add(rumps.MenuItem(REFRESH_TITLE, callback=self.check_all))
        self.menu.add(rumps.MenuItem(OPEN_LOG_TITLE, callback=self._open_log))
        self.menu.add(rumps.separator)
        self.menu.add(rumps.MenuItem(QUIT_TITLE, callback=rumps.quit_application))

    def _render(self, statuses: list[RepoStatus]) -> None:
        """Mutate existing MenuItems in place (Pitfall 10); only add/remove
        item objects when the watched-repo set itself changes, never on a
        plain status refresh.
        """
        has_repos = bool(self.cfg.repos)
        specs = menu_builder.build(statuses, has_repos)

        if not has_repos:
            for repo_path in list(self._repo_items.keys()):
                del self.menu[self._repo_item_keys.pop(repo_path)]
                del self._repo_items[repo_path]
            if self._empty_item is None:
                self._empty_item = rumps.MenuItem(menu_builder.EMPTY_STATE_TITLE)
                self.menu.insert_before(ADD_REPO_TITLE, self._empty_item)
            return

        if self._empty_item is not None:
            del self.menu[self._empty_item.title]
            self._empty_item = None

        current_keys = {status.repo_path for status in statuses}
        for repo_path in list(self._repo_items.keys()):
            if repo_path not in current_keys:
                del self.menu[self._repo_item_keys.pop(repo_path)]
                del self._repo_items[repo_path]

        for status, spec in zip(statuses, specs):
            self._render_row(status, spec)

    def _render_row(self, status: RepoStatus, spec: MenuItemSpec) -> None:
        item = self._repo_items.get(status.repo_path)
        if item is None:
            item = rumps.MenuItem(spec.title, callback=self._repo_click_handler(status.repo_path))
            self._repo_items[status.repo_path] = item
            self._repo_item_keys[status.repo_path] = spec.title
            self.menu.insert_before(ADD_REPO_TITLE, item)
        else:
            item.title = spec.title  # mutate in place, never rebuild (Pitfall 10)

    # -- static menu actions -------------------------------------------------

    def _add_repo(self, _sender) -> None:
        rumps.alert(
            title=ADD_REPO_TITLE,
            message="Add/remove repos is coming in the next slice (Plan 02).",
        )

    def _open_log(self, _sender) -> None:
        subprocess.run(["open", "-e", str(log.log_path())], timeout=10)

    def _repo_click_handler(self, repo_path: str):
        def handler(_sender):
            status = self.statuses.get(repo_path)
            if status is None:
                rumps.alert(title=repo_path, message="Not checked yet.")
                return
            if status.failure_reason:
                rumps.alert(title=status.name, message=status.failure_reason)
                return
            branch_status = status.branch_statuses[0] if status.branch_statuses else None
            if branch_status is None or branch_status.behind == 0:
                rumps.alert(title=status.name, message="Up to date.")
            else:
                rumps.alert(
                    title=status.name,
                    message=f"{branch_status.behind} commits behind ({branch_status.base}).",
                )

        return handler

    # -- polling cycle -------------------------------------------------------

    def check_all(self, _sender) -> None:
        if self._checking:
            return
        self._checking = True
        self.title = "…"
        try:
            log.rotate_if_needed()
            log.append(f"---- {datetime.datetime.now()} ----")

            newly_behind: list[RepoStatus] = []
            statuses: list[RepoStatus] = []

            for repo in self.cfg.repos:
                status = git_ops.check_repo(repo)
                statuses.append(status)

                if status.failure_reason is not None:
                    log.append(f"[FAIL] {status.name}: {status.failure_reason}")
                    continue

                branch_status = status.branch_statuses[0] if status.branch_statuses else None
                if branch_status is None or branch_status.kind == StatusKind.UP_TO_DATE:
                    log.append(f"[OK] {status.name}: up to date")
                else:
                    log.append(
                        f"[OK] {status.name}: {branch_status.behind} behind "
                        f"({branch_status.base})"
                    )
                    newly_behind.append(status)

            self.statuses = {status.repo_path: status for status in statuses}

            if newly_behind:
                # Skeleton scope: one inline notification, no dedupe/state yet (Plan 04).
                first = newly_behind[0]
                bs = first.branch_statuses[0]
                _notify(
                    title=f"{first.name}: {bs.base} has {bs.behind} new commit(s)",
                    subtitle="Base Branch Watch",
                    body=f"{bs.behind} commits behind {bs.base}",
                )

            self._render(statuses)
        finally:
            self._checking = False
            self.title = menu_builder.title_for(list(self.statuses.values()))

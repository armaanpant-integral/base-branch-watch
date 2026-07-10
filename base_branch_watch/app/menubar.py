"""Thin rumps.App glue: Timer, recursive MenuItemSpec render, single inline
notification, real Open Log action.

Keeps app/ free of any git/JSON logic beyond calling core.* (ARCHITECTURE.md
Anti-Pattern 1). The only subprocess uses permitted here are the osascript
notification and the `open -e` Open Log action — all git/config/log state
goes through core.*.
"""

from __future__ import annotations

import datetime
import os
import subprocess

import rumps
from AppKit import NSAlert, NSApplication, NSOpenPanel, NSWindowCollectionBehaviorCanJoinAllSpaces

from base_branch_watch.app import menu_builder
from base_branch_watch.core import config, git_ops, log, state
from base_branch_watch.core.models import MenuItemSpec, RepoStatus, Severity, StatusKind
from base_branch_watch.notify.base import Notifier
from base_branch_watch.notify.osascript_notifier import OsascriptNotifier
from base_branch_watch.runner import batch

ADD_REPO_TITLE = "Add Repo…"
REMOVE_REPO_TITLE = "Remove Repo"
EDIT_BASE_TITLE = "Edit Base Branch(es)"
SET_INTERVAL_TITLE = "Set Interval…"
REFRESH_TITLE = "Refresh Now"
OPEN_LOG_TITLE = "Open Log"
QUIT_TITLE = "Quit"


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
        # Multi-base submenu child rows: repo_path -> {base: rumps.MenuItem}.
        # Populated only for repos whose spec has children (2+ base branches);
        # mutated in place on subsequent renders, same discipline as
        # _repo_items/_repo_item_keys above (Pitfall 10).
        self._repo_child_items: dict[str, dict[str, rumps.MenuItem]] = {}
        self._empty_item: rumps.MenuItem | None = None
        self._checking = False
        self.remove_menu: rumps.MenuItem | None = None
        self.edit_menu: rumps.MenuItem | None = None
        self._notifier: Notifier = OsascriptNotifier()
        self._state: state.State = state.load_state()

        self._build_menu_shell()

        self.timer = rumps.Timer(self.check_all, self.cfg.poll_interval_seconds)
        self.timer.start()
        self.check_all(None)

    # -- menu construction -------------------------------------------------

    def _build_menu_shell(self) -> None:
        """Build the menu once: static items first, then initial repo rows / empty state.

        Static items (starting with Add Repo…) must exist in self.menu before the
        first _render() call — _render()'s empty-state branch inserts relative to
        ADD_REPO_TITLE via insert_before, which raises KeyError if that item hasn't
        been added yet (hit on a true first-launch, zero-repo config).
        """
        self.menu.add(rumps.separator)
        self.menu.add(rumps.MenuItem(ADD_REPO_TITLE, callback=self._add_repo))
        self.remove_menu = rumps.MenuItem(REMOVE_REPO_TITLE)
        self.menu.add(self.remove_menu)
        self._rebuild_remove_submenu()
        self.edit_menu = rumps.MenuItem(EDIT_BASE_TITLE)
        self.menu.add(self.edit_menu)
        self._rebuild_edit_submenu()
        self.menu.add(rumps.MenuItem(SET_INTERVAL_TITLE, callback=self._set_interval))
        self.menu.add(rumps.MenuItem(REFRESH_TITLE, callback=self.check_all))
        self.menu.add(rumps.MenuItem(OPEN_LOG_TITLE, callback=self._open_log))
        self.menu.add(rumps.separator)
        self.menu.add(rumps.MenuItem(QUIT_TITLE, callback=rumps.quit_application))
        self._render(list(self.statuses.values()))

    def _rebuild_remove_submenu(self) -> None:
        """Rebuild the Remove Repo submenu's item objects to match cfg.repos.

        This is the sanctioned full-rebuild trigger for a watch-list change
        (add/remove repo), distinct from the per-cycle in-place title mutation
        used by _render (Pitfall 10).
        """
        # rumps.MenuItem lazily creates its backing NSMenu on first __setitem__;
        # .clear() dereferences it unconditionally, so guard against the
        # never-had-an-item case (e.g. first launch with zero repos).
        if len(self.remove_menu) > 0:
            self.remove_menu.clear()
        for repo in self.cfg.repos:
            name = os.path.basename(repo.repo_path.rstrip("/"))
            self.remove_menu.add(
                rumps.MenuItem(name, callback=self._remove_repo_click_handler(repo.repo_path))
            )

    def _rebuild_edit_submenu(self) -> None:
        """Rebuild the Edit Base Branch(es) submenu's item objects to match
        cfg.repos — same full-rebuild-on-watch-list-change discipline as
        _rebuild_remove_submenu (a watch-list change, not a per-cycle status
        refresh, so Pitfall 10's in-place-mutation rule doesn't apply here)."""
        if len(self.edit_menu) > 0:
            self.edit_menu.clear()
        for repo in self.cfg.repos:
            name = os.path.basename(repo.repo_path.rstrip("/"))
            self.edit_menu.add(
                rumps.MenuItem(
                    name, callback=self._edit_base_branches_click_handler(repo.repo_path)
                )
            )

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
                self._repo_child_items.pop(repo_path, None)
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
                self._repo_child_items.pop(repo_path, None)

        for status, spec in zip(statuses, specs):
            self._render_row(status, spec)

    def _render_row(self, status: RepoStatus, spec: MenuItemSpec) -> None:
        item = self._repo_items.get(status.repo_path)
        if item is None:
            if spec.children:
                # Submenu parent: NO callback (Pitfall — a native NSMenu
                # submenu parent only expands; a callback would conflict).
                item = rumps.MenuItem(spec.title)
                self._repo_child_items[status.repo_path] = self._build_submenu_children(
                    item, status.repo_path, spec.children
                )
            else:
                item = rumps.MenuItem(
                    spec.title, callback=self._repo_click_handler(status.repo_path)
                )
            self._repo_items[status.repo_path] = item
            self._repo_item_keys[status.repo_path] = spec.title
            self.menu.insert_before(ADD_REPO_TITLE, item)
        else:
            item.title = spec.title  # mutate in place, never rebuild (Pitfall 10)
            if spec.children:
                self._update_submenu_children(item, status.repo_path, spec.children)

    def _build_submenu_children(
        self, parent: rumps.MenuItem, repo_path: str, children: list[MenuItemSpec]
    ) -> dict[str, rumps.MenuItem]:
        """Populate parent's own sub-items from children, keyed by base name.

        parent is itself a Menu (rumps.MenuItem subclasses Menu), so it
        supports the same add()/insert_before()/__setitem__ surface as
        self.menu — same construction pattern as the top-level repo rows.
        """
        child_items: dict[str, rumps.MenuItem] = {}
        for child_spec in children:
            base = self._base_from_callback_key(child_spec.callback_key)
            child_item = rumps.MenuItem(
                child_spec.title, callback=self._child_click_handler(repo_path, base)
            )
            parent.add(child_item)
            child_items[base] = child_item
        return child_items

    def _update_submenu_children(
        self, parent: rumps.MenuItem, repo_path: str, children: list[MenuItemSpec]
    ) -> None:
        """Mutate existing child MenuItems in place (Pitfall 10) — same
        discipline as _render_row's top-level title mutation. Falls back to
        a full rebuild if the configured base set itself has changed (rare —
        only happens if a repo's base branches are edited without going
        through the remove/re-add path that already clears stale state)."""
        child_items = self._repo_child_items.setdefault(repo_path, {})
        current_bases = {self._base_from_callback_key(c.callback_key) for c in children}
        if set(child_items.keys()) != current_bases:
            if len(parent) > 0:
                parent.clear()
            self._repo_child_items[repo_path] = self._build_submenu_children(
                parent, repo_path, children
            )
            return
        for child_spec in children:
            base = self._base_from_callback_key(child_spec.callback_key)
            child_items[base].title = child_spec.title

    @staticmethod
    def _base_from_callback_key(callback_key: str | None) -> str:
        """Child MenuItemSpec.callback_key is f"{repo_path}::{base}" (see
        menu_builder._child_row) — the base name is everything after the
        last "::" (repo paths may contain "::" only in pathological cases,
        so split from the right to stay robust)."""
        assert callback_key is not None
        return callback_key.rsplit("::", 1)[-1]

    # -- static menu actions -------------------------------------------------

    @staticmethod
    def _activate() -> None:
        """Bring the app frontmost before showing any dialog. Necessary but
        NOT sufficient for rumps.alert — see _show_alert."""
        NSApplication.sharedApplication().activateIgnoringOtherApps_(True)

    @staticmethod
    def _show_alert(title: str, message: str, ok: str = "OK", cancel: str | None = None) -> int:
        """Direct NSAlert construction instead of rumps.alert, so the window's
        collectionBehavior can be forced to join the user's CURRENT macOS
        Space. activateIgnoringOtherApps_ alone brings the app forward but
        does NOT move an already-placed window onto the active Space — a
        background LSUIElement app's alert otherwise pops up on whatever
        Space it last belonged to, forcing a manual Space switch to see it.
        Returns 1 if `ok` was clicked (matches rumps.alert's convention used
        by this file's existing `!= 1` checks), 0 otherwise."""
        NSApplication.sharedApplication().activateIgnoringOtherApps_(True)
        alert = NSAlert.alloc().init()
        alert.setMessageText_(title)
        alert.setInformativeText_(message)
        alert.addButtonWithTitle_(ok)
        if cancel:
            alert.addButtonWithTitle_(cancel)
        alert.window().setCollectionBehavior_(NSWindowCollectionBehaviorCanJoinAllSpaces)
        response = alert.runModal()
        return 1 if response == 1000 else 0

    def _add_repo(self, _sender) -> None:
        self._activate()
        panel = NSOpenPanel.openPanel()
        panel.setCanChooseDirectories_(True)
        panel.setCanChooseFiles_(False)
        panel.setAllowsMultipleSelection_(False)
        panel.setPrompt_("Select Repo")
        result = panel.runModal()
        if result != 1:
            return
        repo_path = panel.URLs()[0].path()

        if not os.path.isdir(os.path.join(repo_path, ".git")):
            self._show_alert(
                title="Not a Git Repository",
                message=f"{repo_path}\n\nChoose a folder that is a git repository.",
            )
            return

        repo_name = os.path.basename(repo_path.rstrip("/"))
        detected = git_ops.detect_default_branch(repo_path)

        resp = rumps.Window(
            title="Add Repo",
            message=(
                f"Base branch(es) to watch for {repo_name} — comma-separated for multiple:"
            ),
            default_text=detected or "main",
            ok="Add",
            cancel="Cancel",
        ).run()
        if not resp.clicked:
            return
        raw_text = resp.text.strip()
        if not raw_text:
            return

        parsed = config.parse_base_branches(raw_text)
        if not parsed:
            return

        self.cfg = config.add_repo(self.cfg, repo_path, parsed)
        config.save_config(self.cfg)
        self._rebuild_remove_submenu()
        self._rebuild_edit_submenu()
        self.check_all(None)

    def _remove_repo_click_handler(self, repo_path: str):
        def handler(_sender):
            self._activate()
            name = os.path.basename(repo_path.rstrip("/"))
            resp = self._show_alert(
                title=f"Remove {name}?",
                message="Stop watching this repo. Nothing on disk or in git history is changed.",
                ok="Remove",
                cancel="Cancel",
            )
            if resp != 1:
                return
            self.cfg = config.remove_repo(self.cfg, repo_path)
            config.save_config(self.cfg)
            self.statuses.pop(repo_path, None)
            self._rebuild_remove_submenu()
            self._rebuild_edit_submenu()
            self._render(list(self.statuses.values()))

        return handler

    def _edit_base_branches_click_handler(self, repo_path: str):
        """Change an already-watched repo's base branch(es) without going
        through Remove + re-Add. Reuses config.add_repo's replace-not-
        duplicate semantics — editing IS adding with the same repo_path."""

        def handler(_sender):
            self._activate()
            name = os.path.basename(repo_path.rstrip("/"))
            current = next((r for r in self.cfg.repos if r.repo_path == repo_path), None)
            if current is None:
                return
            resp = rumps.Window(
                title="Edit Base Branch(es)",
                message=f"Base branch(es) to watch for {name} — comma-separated for multiple:",
                default_text=", ".join(current.base_branches),
                ok="Save",
                cancel="Cancel",
            ).run()
            if not resp.clicked:
                return
            parsed = config.parse_base_branches(resp.text.strip())
            if not parsed:
                return
            self.cfg = config.add_repo(self.cfg, repo_path, parsed)
            config.save_config(self.cfg)
            self.check_all(None)

        return handler

    def _open_log(self, _sender) -> None:
        subprocess.run(["open", "-e", str(log.log_path())], timeout=10)

    def _set_interval(self, _sender) -> None:
        self._activate()
        resp = rumps.Window(
            title="Set Interval",
            message="Polling interval in seconds (minimum 30):",
            default_text=str(self.cfg.poll_interval_seconds),
            ok="Save",
            cancel="Cancel",
        ).run()
        if not resp.clicked:
            return
        raw_text = resp.text.strip()
        try:
            self.cfg = config.set_poll_interval(self.cfg, raw_text)
        except ValueError:
            self._show_alert(title="Invalid Interval", message=f"{raw_text!r} is not a number.")
            return
        config.save_config(self.cfg)
        # Live-update: stop the existing Timer and start a new one at the new
        # interval so the change takes effect without an app relaunch.
        self.timer.stop()
        self.timer = rumps.Timer(self.check_all, self.cfg.poll_interval_seconds)
        self.timer.start()

    def _repo_click_handler(self, repo_path: str):
        def handler(_sender):
            self._activate()
            status = self.statuses.get(repo_path)
            if status is None:
                self._show_alert(title=repo_path, message="Not checked yet.")
                return
            if status.failure_reason:
                self._show_alert(title=status.name, message=status.failure_reason)
                return
            branch_status = status.worst_branch_status
            if branch_status is None:
                self._show_alert(title=status.name, message="Up to date.")
            elif branch_status.kind == StatusKind.CHECK_FAILED:
                self._show_alert(
                    title=status.name, message=branch_status.reason or "unknown error"
                )
            elif branch_status.kind == StatusKind.DIVERGED:
                self._show_alert(
                    title=status.name,
                    message=(
                        f"Diverged — {branch_status.behind} behind, "
                        f"{branch_status.ahead_of_base} ahead ({branch_status.base})."
                    ),
                )
            elif branch_status.behind == 0:
                self._show_alert(title=status.name, message="Up to date.")
            else:
                self._show_alert(
                    title=status.name,
                    message=f"{branch_status.behind} commits behind ({branch_status.base}).",
                )

        return handler

    def _child_click_handler(self, repo_path: str, base: str):
        """Per-base submenu row click — same message vocabulary as
        _repo_click_handler, but scoped to this one base rather than the
        repo's worst status."""

        def handler(_sender):
            self._activate()
            status = self.statuses.get(repo_path)
            if status is None:
                self._show_alert(title=repo_path, message="Not checked yet.")
                return
            bs = next((b for b in status.branch_statuses if b.base == base), None)
            title = f"{status.name} ({base})"
            if bs is None:
                self._show_alert(title=title, message="Not checked yet.")
            elif bs.kind == StatusKind.CHECK_FAILED:
                self._show_alert(title=title, message=bs.reason or "unknown error")
            elif bs.kind == StatusKind.DIVERGED:
                self._show_alert(
                    title=title,
                    message=f"Diverged — {bs.behind} behind, {bs.ahead_of_base} ahead.",
                )
            elif bs.behind == 0:
                self._show_alert(title=title, message="Up to date.")
            else:
                self._show_alert(title=title, message=f"{bs.behind} commits behind.")

        return handler

    # -- polling cycle -------------------------------------------------------

    def _compute_notify_subset(self, statuses: list[RepoStatus]) -> list[RepoStatus]:
        """Statuses needing attention whose SHA-based dedupe says "notify now".

        Mutates self._state via state.mark_notified for every base whose
        current SHA is included in the returned subset, so a repeated cycle
        with an unchanged base SHA does not re-notify (must_have truth).
        Repo-level or per-base CHECK_FAILED has no SHA to dedupe against, so
        it always counts as "needs notification" while it keeps failing.

        The unpushed-commit-count axis (status.unpushed) is deduped
        independently of base SHAs (WR-01) — a repo whose worst status is
        UNPUSHED-only re-notifies whenever the unpushed count itself changes,
        even if every base's SHA is unchanged.
        """
        subset: list[RepoStatus] = []
        for status in statuses:
            if status.severity == Severity.OK:
                continue

            if status.failure_reason is not None:
                subset.append(status)
                continue

            needs = False
            base_shas: dict[str, str] = {}
            for bs in status.branch_statuses:
                if bs.kind == StatusKind.CHECK_FAILED:
                    needs = True
                    continue
                sha = state.base_head_sha(status.repo_path, bs.base)
                if sha is None:
                    continue
                base_shas[bs.base] = sha
                if state.should_notify(self._state, status.repo_path, bs.base, sha):
                    needs = True

            if status.unpushed > 0:
                if state.should_notify_unpushed(self._state, status.repo_path, status.unpushed):
                    needs = True
            else:
                self._state = state.clear_notified_unpushed(self._state, status.repo_path)

            if needs:
                subset.append(status)
                for base, sha in base_shas.items():
                    self._state = state.mark_notified(self._state, status.repo_path, base, sha)
                if status.unpushed > 0:
                    self._state = state.mark_notified_unpushed(
                        self._state, status.repo_path, status.unpushed
                    )

        return subset

    def _log_status_line(self, status: RepoStatus) -> str:
        if status.failure_reason is not None:
            return f"[FAIL] {status.name}: {status.failure_reason}"
        bs = status.worst_branch_status
        if bs is None or bs.kind == StatusKind.UP_TO_DATE:
            suffix = "up to date" if status.unpushed == 0 else f"{status.unpushed} unpushed"
        elif bs.kind == StatusKind.DIVERGED:
            suffix = f"diverged — {bs.behind} behind, {bs.ahead_of_base} ahead ({bs.base})"
        elif bs.kind == StatusKind.CHECK_FAILED:
            suffix = f"check failed — {bs.reason or 'unknown error'} ({bs.base})"
        else:
            suffix = f"{bs.behind} behind ({bs.base})"
        return f"[OK] {status.name}: {suffix}"

    def check_all(self, _sender) -> None:
        if self._checking:
            return
        self._checking = True
        self.title = "…"
        try:
            log.rotate_if_needed()
            log.append(f"---- {datetime.datetime.now()} ----")

            statuses = batch.check_all(self.cfg.repos)
            for status in statuses:
                log.append(self._log_status_line(status))

            self.statuses = {status.repo_path: status for status in statuses}

            subset = self._compute_notify_subset(statuses)
            if subset:
                self._notifier.send_digest(subset)
                state.save_state(self._state)

            self._render(statuses)
        except Exception as exc:  # noqa: BLE001 - top-level poll-cycle guard, see CR-04
            # Never let a single bad cycle kill the whole polling loop (the
            # "never raises" convention core/git_ops.py established). Log
            # and swallow; state/title still reset via finally below.
            log.append(f"[FAIL] check_all cycle raised: {exc!r}")
        finally:
            self._checking = False
            self.title = menu_builder.title_for(list(self.statuses.values()))

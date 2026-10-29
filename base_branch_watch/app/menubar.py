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
import sys
import time

import rumps
from AppKit import NSAlert, NSApplication, NSOpenPanel, NSWindowCollectionBehaviorCanJoinAllSpaces

from base_branch_watch.app import menu_builder
from base_branch_watch.core import (
    config,
    git_ops,
    hook_install,
    log,
    notify_policy,
    self_update,
    state,
    status_text,
)
from base_branch_watch.core.models import (
    BranchStatus,
    MenuItemSpec,
    RepoStatus,
    StatusKind,
)
from base_branch_watch.notify.base import Notifier
from base_branch_watch.notify.osascript_notifier import OsascriptNotifier
from base_branch_watch.runner.poll_cycle import PollCycle

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
        # PR-status row tracking (D-04/D-05/D-06/D-12) — parallel to the
        # _repo_items/_repo_item_keys/_repo_child_items trio above, keyed the
        # same way (by repo_path), same in-place-mutation discipline
        # (Pitfall 10).
        self._pr_items: dict[str, rumps.MenuItem] = {}
        self._pr_item_keys: dict[str, str] = {}
        self._pr_child_items: dict[str, dict[str, rumps.MenuItem]] = {}
        # Stale-branch row, one per repo that has stale branches: same keying as
        # the PR row (by repo_path; the key is the title at creation time).
        self._stale_items: dict[str, rumps.MenuItem] = {}
        self._stale_item_keys: dict[str, str] = {}
        # The poll cycle's own memory (last PR status per repo, D-13's PR-check
        # floor timestamps) lives in runner.poll_cycle.PollCycle.
        self._cycle = PollCycle()
        self._empty_item: rumps.MenuItem | None = None
        self._checking = False
        self.remove_menu: rumps.MenuItem | None = None
        self.edit_menu: rumps.MenuItem | None = None
        self._notifier: Notifier = OsascriptNotifier()
        self._state: state.State = state.load_state()
        # Self-update check: None checkout root = not a source install, so the
        # check is disabled. Monotonic -inf sentinel (WR-03) => first cycle
        # always checks ("once at launch"), then at most once per interval.
        self._checkout_root: str | None = self_update.checkout_root()
        self._last_update_checked_at = float("-inf")
        self._update_info: self_update.UpdateInfo | None = None
        self._update_item: rumps.MenuItem | None = None

        self._build_menu_shell()
        self._backfill_hooks()

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
            self._remove_pr_row(list(self._pr_items.keys()))
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
        # PR rows track the same watched-repo set 1:1 (D-01/D-04) — remove
        # any PR row for a repo no longer watched, parallel to the git-row
        # cleanup above.
        stale_pr_repos = [rp for rp in self._pr_items if rp not in current_keys]
        self._remove_pr_row(stale_pr_repos)

        for status, spec in zip(statuses, specs):
            self._render_row(status, spec)

    def _remove_pr_row(self, repo_paths: list[str]) -> None:
        for repo_path in repo_paths:
            key = self._pr_item_keys.pop(repo_path, None)
            if key is not None:
                del self.menu[key]
            self._pr_items.pop(repo_path, None)
            self._pr_child_items.pop(repo_path, None)
            self._cycle.forget([repo_path])

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
        """Populate parent's own sub-items from children, keyed by _child_key.

        parent is itself a Menu (rumps.MenuItem subclasses Menu), so it
        supports the same add()/insert_before()/__setitem__ surface as
        self.menu — same construction pattern as the top-level repo rows.

        A child_spec falls into one of three shapes:
        - has its own children (e.g. a CONFLICT_RISK base row nested inside
          a multi-base repo's submenu, itself listing overlapping files):
          build it as a nested submenu parent (no callback) and recurse.
        - callback_key set, no children: the existing "one clickable child
          per base" leaf.
        - callback_key None, no children: a plain informational leaf
          (conflict-path row, "…and N more" overflow row, or a single-base
          repo's own conflict-path children) — never clickable.
        """
        child_items: dict[str, rumps.MenuItem] = {}
        for child_spec in children:
            key = self._child_key(child_spec)
            if child_spec.children:
                child_item = rumps.MenuItem(child_spec.title)
                self._build_submenu_children(child_item, repo_path, child_spec.children)
            elif child_spec.callback_key is not None:
                base = self._base_from_callback_key(child_spec.callback_key)
                child_item = rumps.MenuItem(
                    child_spec.title, callback=self._child_click_handler(repo_path, base)
                )
            else:
                child_item = rumps.MenuItem(child_spec.title)
            parent.add(child_item)
            child_items[key] = child_item
        return child_items

    def _update_submenu_children(
        self, parent: rumps.MenuItem, repo_path: str, children: list[MenuItemSpec]
    ) -> None:
        """Mutate existing child MenuItems in place (Pitfall 10) — same
        discipline as _render_row's top-level title mutation. Falls back to
        a full rebuild if the child key set itself has changed (rare —
        base branches edited, or a base's conflict-risk state just
        toggled on/off, changing whether it has nested children)."""
        child_items = self._repo_child_items.setdefault(repo_path, {})
        current_keys = {self._child_key(c) for c in children}
        if set(child_items.keys()) != current_keys:
            if len(parent) > 0:
                parent.clear()
            self._repo_child_items[repo_path] = self._build_submenu_children(
                parent, repo_path, children
            )
            return
        for child_spec in children:
            item = child_items[self._child_key(child_spec)]
            if child_spec.children:
                # Nested list contents (which files overlap) may have
                # changed even though the parent row's own key is stable;
                # polling is infrequent enough that a full nested rebuild
                # is cheap and avoids stale sub-items.
                if len(item) > 0:
                    item.clear()
                self._build_submenu_children(item, repo_path, child_spec.children)
            else:
                item.title = child_spec.title

    def _child_key(self, child_spec: MenuItemSpec) -> str:
        """Stable per-poll identity for a submenu child. Callback-bearing
        rows (one clickable child per base) key off callback_key's base
        segment; non-clickable rows (conflict-path leaves, the overflow
        row) have no callback_key and are keyed by title instead — title is
        unique within any one conflict-path list."""
        if child_spec.callback_key is not None:
            return self._base_from_callback_key(child_spec.callback_key)
        return child_spec.title

    @staticmethod
    def _base_from_callback_key(callback_key: str | None) -> str:
        """Child MenuItemSpec.callback_key is f"{repo_path}::{base}" (see
        menu_builder._child_row) — the base name is everything after the
        last "::" (repo paths may contain "::" only in pathological cases,
        so split from the right to stay robust). Only called on a
        known-non-None callback_key (see _child_key / _build_submenu_children
        callers) — the assert documents that contract."""
        assert callback_key is not None
        return callback_key.rsplit("::", 1)[-1]

    # -- PR row (D-05/D-06/D-07/D-02/D-12) ------------------------------------

    def _render_pr_row(self, repo_path: str, spec: MenuItemSpec) -> None:
        """Insert/mutate the PR-status row for one repo, placed immediately
        after that repo's git-status row via insert_after (called only after
        _render(statuses) has already (re)created the git row for repo_path,
        so self._repo_item_keys[repo_path] is always populated by this
        point). PR children (Checks/Review/Mergeable, callback_key=None
        always) are never clickable — unlike the generic
        _build_submenu_children/_child_key machinery, PR children are keyed
        by their fixed position (order is locked: Checks, Review, Mergeable —
        04-UI-SPEC.md), not by title, since a segment's glyph/text can change
        every poll while its position never does.
        """
        item = self._pr_items.get(repo_path)
        if item is None:
            # Submenu parent (children present) or flat row (no children) —
            # never a callback either way (_pr_row never sets callback_key).
            item = rumps.MenuItem(spec.title)
            if spec.children:
                self._pr_child_items[repo_path] = self._build_pr_children(item, spec.children)
            self._pr_items[repo_path] = item
            self._pr_item_keys[repo_path] = spec.title
            anchor = self._repo_item_keys.get(repo_path)
            if anchor is not None:
                self.menu.insert_after(anchor, item)
            else:
                self.menu.insert_before(ADD_REPO_TITLE, item)
        else:
            item.title = spec.title  # mutate in place, never rebuild (Pitfall 10)
            if spec.children:
                self._update_pr_children(item, repo_path, spec.children)
            elif repo_path in self._pr_child_items:
                # Transitioned from an OPEN row (with children) to a flat
                # NO_PR/CHECK_FAILED row — drop the now-stale children.
                if len(item) > 0:
                    item.clear()
                self._pr_child_items.pop(repo_path, None)

    # -- stale-branch row -----------------------------------------------------

    def _render_stale_row(self, repo_path: str, spec: MenuItemSpec) -> None:
        """Insert or mutate one repo's stale-branch row, placed after its PR row
        (or after its git row when it has no PR row). Children are never clickable."""
        item = self._stale_items.get(repo_path)
        if item is None:
            item = rumps.MenuItem(spec.title)
            for child_spec in spec.children:
                item.add(rumps.MenuItem(child_spec.title))
            self._stale_items[repo_path] = item
            self._stale_item_keys[repo_path] = spec.title
            anchor = self._pr_item_keys.get(repo_path) or self._repo_item_keys.get(repo_path)
            if anchor is not None:
                self.menu.insert_after(anchor, item)
            else:
                self.menu.insert_before(ADD_REPO_TITLE, item)
            return
        item.title = spec.title  # mutate in place, never rebuild the parent
        if [child.title for child in item.values()] != [child.title for child in spec.children]:
            item.clear()
            for child_spec in spec.children:
                item.add(rumps.MenuItem(child_spec.title))

    @staticmethod
    def _build_pr_children(
        parent: rumps.MenuItem, children: list[MenuItemSpec]
    ) -> list[rumps.MenuItem]:
        items: list[rumps.MenuItem] = []
        for child_spec in children:
            child_item = rumps.MenuItem(child_spec.title)  # never clickable
            parent.add(child_item)
            items.append(child_item)
        return items

    def _update_pr_children(
        self, parent: rumps.MenuItem, repo_path: str, children: list[MenuItemSpec]
    ) -> None:
        """Mutate the 3 fixed-position PR children in place (Pitfall 10).
        Falls back to a full rebuild only if the CHILD COUNT itself changed
        (never happens in Plan 01 scope — OPEN always yields exactly 3 — but
        this keeps the method forward-compatible/never-crashing)."""
        items = self._pr_child_items.get(repo_path)
        if items is None or len(items) != len(children):
            if len(parent) > 0:
                parent.clear()
            self._pr_child_items[repo_path] = self._build_pr_children(parent, children)
            return
        for item, child_spec in zip(items, children):
            item.title = child_spec.title

    # -- pre-push hook install/uninstall (HOOK-01, D-01/D-03/D-04) -----------

    def _install_hook_for(self, repo_path: str) -> None:
        """Trigger-only: the install logic lives in core.hook_install. Bakes
        sys.executable, the running app's own interpreter, which is
        guaranteed to import base_branch_watch. Never raises (hook_install
        returns a result) so a single bad repo can't abort the startup
        backfill loop or the add-repo flow (T-03-12)."""
        result = hook_install.install(repo_path, sys.executable)
        if not result.ok:
            log.append(f"[FAIL] pre-push hook install failed for {repo_path}: {result.message}")

    def _uninstall_hook_for(self, repo_path: str) -> None:
        """Symmetric with _install_hook_for (D-04); never raises."""
        result = hook_install.uninstall(repo_path)
        if not result.ok:
            log.append(f"[FAIL] pre-push hook uninstall failed for {repo_path}: {result.message}")

    def _backfill_hooks(self) -> None:
        """Startup backfill: install the pre-push hook into every already-
        configured repo (idempotent re-install, foreign-hook-safe) so
        "every watched repo" holds immediately on the Phase 3 upgrade, not
        only for repos added after it ships. Safe to run on every launch --
        re-installing a bbwatch-marked hook simply rewrites it, and a
        foreign hook is refused and left untouched (D-03)."""
        for repo in self.cfg.repos:
            self._install_hook_for(repo.repo_path)

    # -- self-update ---------------------------------------------------------

    def _maybe_check_for_update(self) -> None:
        """Daily check of the app's own checkout against origin; surfaces a
        menu row plus one notification per new upstream SHA. Never raises --
        an update-check problem must not disturb the repo poll cycle."""
        if self._checkout_root is None:
            return
        now = time.monotonic()
        if now - self._last_update_checked_at < self_update.UPDATE_CHECK_INTERVAL_SECONDS:
            return
        self._last_update_checked_at = now
        try:
            self._update_info = self_update.check_for_update(self._checkout_root)
            self._render_update_item(self._update_info)
            info = self._update_info
            if info is not None:
                decision = notify_policy.decide_update(self._state, info.remote_sha)
                if decision.notify:
                    self._notifier.send_message(
                        "Base Branch Watch update available",
                        f"{info.behind} new commit(s) on {info.base} - open the menu for details",
                    )
                self._persist_state(decision.state)
        except Exception as exc:  # noqa: BLE001 - isolate from the poll cycle
            log.append(f"[FAIL] update check raised: {exc!r}")

    def _render_update_item(self, info: self_update.UpdateInfo | None) -> None:
        """Add/replace/remove the 'Update available' row. Rebuilt rather than
        mutated: its title (commit count) is its rumps dict key (see the
        _repo_item_keys note in __init__)."""
        if self._update_item is not None:
            del self.menu[self._update_item.title]
            self._update_item = None
        if info is None:
            return
        self._update_item = rumps.MenuItem(
            f"⬆️ Update available ({info.behind} new commit{'s' if info.behind != 1 else ''})…",
            callback=self._show_update_info,
        )
        self.menu.insert_before(OPEN_LOG_TITLE, self._update_item)

    def _show_update_info(self, _sender) -> None:
        info = self._update_info
        if info is None:
            return
        commits = "\n".join(f"  - {subject}" for subject in info.subjects)
        more = info.behind - len(info.subjects)
        if more > 0:
            commits += f"\n  ...and {more} more"
        self._show_alert(
            "Update available",
            f"{info.behind} new commit(s) on origin/{info.base}:\n{commits}\n\n"
            f"To update:\n"
            f"1. cd {self._checkout_root} && git pull --ff-only\n"
            f"2. Choose Quit from this menu - launchd restarts the app on the new code.",
        )

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

    @staticmethod
    def _show_text_prompt(title: str, message: str, default_text: str, ok: str, cancel: str):
        """rumps.Window wrapper that forces the backing window to join the
        user's CURRENT macOS Space, mirroring _show_alert's fix. rumps.Window
        builds an NSAlert (self._alert) at construction time and its own
        run() reads self._alert.window() before calling runModal() (to set
        dark-mode appearance) -- proof the backing window is available and
        mutable pre-run, the same seam _show_alert uses directly. Delegates
        all UX (accessory text field, buttons) and the returned
        Response(.clicked, .text) shape to rumps.Window unchanged."""
        window = rumps.Window(
            title=title,
            message=message,
            default_text=default_text,
            ok=ok,
            cancel=cancel,
        )
        window._alert.window().setCollectionBehavior_(NSWindowCollectionBehaviorCanJoinAllSpaces)
        return window.run()

    def _add_repo(self, _sender) -> None:
        self._activate()
        panel = NSOpenPanel.openPanel()
        panel.setCanChooseDirectories_(True)
        panel.setCanChooseFiles_(False)
        panel.setAllowsMultipleSelection_(False)
        panel.setPrompt_("Select Repo")
        panel.setCollectionBehavior_(NSWindowCollectionBehaviorCanJoinAllSpaces)
        result = panel.runModal()
        if result != 1:
            return
        repo_path = panel.URLs()[0].path()

        if git_ops.repo_toplevel(repo_path) is None:
            self._show_alert(
                title="Not a Git Repository",
                message=f"{repo_path}\n\nChoose a folder that is a git repository.",
            )
            return

        repo_name = os.path.basename(repo_path.rstrip("/"))
        detected = git_ops.detect_default_branch(repo_path)

        resp = self._show_text_prompt(
            title="Add Repo",
            message=(
                f"Base branch(es) to watch for {repo_name} - comma-separated for multiple:"
            ),
            default_text=detected or "main",
            ok="Add",
            cancel="Cancel",
        )
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
        self._install_hook_for(repo_path)
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
            self._uninstall_hook_for(repo_path)
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
            resp = self._show_text_prompt(
                title="Edit Base Branch(es)",
                message=f"Base branch(es) to watch for {name} - comma-separated for multiple:",
                default_text=", ".join(current.base_branches),
                ok="Save",
                cancel="Cancel",
            )
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
        resp = self._show_text_prompt(
            title="Set Interval",
            message="Polling interval in seconds (minimum 30):",
            default_text=str(self.cfg.poll_interval_seconds),
            ok="Save",
            cancel="Cancel",
        )
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

    @staticmethod
    def _alert_message(bs: BranchStatus, summary: str) -> str:
        """Alert body: a failure shows its bare reason, a clean base a sentence,
        anything else the shared status phrase (so unpushed counts still show)."""
        if bs.kind == StatusKind.CHECK_FAILED:
            return bs.reason or "unknown error"
        if summary == "up to date":
            return "Up to date."
        return summary

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
            else:
                self._show_alert(
                    title=status.name,
                    message=self._alert_message(branch_status, status_text.repo_summary(status)),
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
            else:
                summary = status_text.base_summary(bs, show_base=False)
                self._show_alert(title=title, message=self._alert_message(bs, summary))

        return handler

    # -- polling cycle -------------------------------------------------------

    def _persist_state(self, next_state: state.State) -> None:
        """Adopt and save next_state only when it differs (send-then-persist, so a
        failed send never marks anything notified)."""
        if next_state != self._state:
            self._state = next_state
            state.save_state(self._state)

    def check_all(self, _sender) -> None:
        if self._checking:
            return
        self._checking = True
        self.title = "…"
        try:
            log.rotate_if_needed()
            log.append(f"---- {datetime.datetime.now()} ----")

            result = self._cycle.run(self.cfg.repos, self._state, time.monotonic())
            for line in result.log_lines:
                log.append(line)
            self.statuses = {status.repo_path: status for status in result.statuses}

            if result.digest_statuses:
                self._notifier.send_digest(result.digest_statuses)
            self._persist_state(result.state)

            self._render(result.statuses)
            # PR row is a second, independent row per repo (D-08) - rendered
            # after the git row so its insert_after anchor already exists.
            for status in result.statuses:
                pr_status_for_repo = result.pr_statuses.get(status.repo_path)
                if pr_status_for_repo is not None:
                    pr_spec = menu_builder._pr_row(pr_status_for_repo, status.name)
                    self._render_pr_row(status.repo_path, pr_spec)
            for status in result.statuses:
                stale_for_repo = result.stale_statuses.get(status.repo_path)
                if stale_for_repo is not None:
                    stale_spec = menu_builder.stale_row(stale_for_repo, status.name)
                    if stale_spec is not None:
                        self._render_stale_row(status.repo_path, stale_spec)
            self._maybe_check_for_update()
        except Exception as exc:  # noqa: BLE001 - top-level poll-cycle guard, see CR-04
            # Never let a single bad cycle kill the whole polling loop (the
            # "never raises" convention core/git_ops.py established). Log
            # and swallow; state/title still reset via finally below.
            log.append(f"[FAIL] check_all cycle raised: {exc!r}")
        finally:
            self._checking = False
            self.title = menu_builder.title_for(list(self.statuses.values()))

"""Wires the dashboard popover onto the status-item button.

Left click toggles the popover; right or control click still pops up the full
rumps menu, so every existing action (Remove, Edit Base, Interval, Open Log,
Quit) stays reachable. If installation fails, the status item keeps its menu.
"""

from __future__ import annotations

from collections.abc import Callable

from AppKit import (
    NSApplication,
    NSEventMaskLeftMouseUp,
    NSEventMaskRightMouseUp,
    NSEventModifierFlagControl,
    NSEventTypeRightMouseUp,
    NSMenu,
)

from base_branch_watch.app.panel_model import PanelModel
from base_branch_watch.app.panel_view import ActionTarget, DashboardPanel
from base_branch_watch.core import log


class PanelHost:
    def __init__(
        self,
        status_item,
        get_model: Callable[[], PanelModel],
        get_seconds_since_check: Callable[[], float | None],
        on_repo_click: Callable[[str], None],
        on_refresh: Callable[[], None],
        on_add_repo: Callable[[], None],
    ) -> None:
        self._status_item = status_item
        self._menu: NSMenu = status_item.menu()
        self._button = status_item.button()
        self._get_model = get_model
        self._get_seconds_since_check = get_seconds_since_check
        self._panel = DashboardPanel(
            on_repo_click=lambda path: self._close_then(on_repo_click, path),
            on_refresh=on_refresh,
            on_add_repo=lambda: self._close_then(on_add_repo),
            on_more=lambda _sender: self._pop_menu(),
        )
        self._target = ActionTarget.alloc().initWithCallbacks_([self._on_status_click])

    def install(self) -> None:
        self._status_item.setMenu_(None)
        self._button.setTarget_(self._target)
        self._button.setAction_("fire:")
        self._button.setTag_(0)
        self._button.sendActionOn_(NSEventMaskLeftMouseUp | NSEventMaskRightMouseUp)

    def refresh(self) -> None:
        """Re-render in place if the popover is open (called after each poll cycle)."""
        if self._panel.is_shown:
            self._panel.update(self._get_model(), self._get_seconds_since_check())

    def _close_then(self, action: Callable, *args) -> None:
        self._panel.close()
        action(*args)

    def _on_status_click(self, _sender) -> None:
        try:
            event = NSApplication.sharedApplication().currentEvent()
            # A synthesized click (accessibility) may carry no event: treat as left click.
            is_right = event is not None and event.type() == NSEventTypeRightMouseUp
            is_ctrl = event is not None and bool(event.modifierFlags() & NSEventModifierFlagControl)
            if is_right or is_ctrl:
                self._pop_menu()
                return
            if not self._panel.is_shown:
                self._panel.update(self._get_model(), self._get_seconds_since_check())
            self._panel.show(self._button)
        except Exception as exc:  # noqa: BLE001 - never raise out of an AppKit action
            log.append(f"[FAIL] dashboard panel click raised: {exc!r}")

    def _pop_menu(self) -> None:
        self._panel.close()
        event = NSApplication.sharedApplication().currentEvent()
        if event is None:
            log.append("[FAIL] dashboard panel could not open menu: no current event")
            return
        NSMenu.popUpContextMenu_withEvent_forView_(self._menu, event, self._button)

"""AppKit dashboard popover: renders a panel_model.PanelModel. No decisions live here.

Frame-based layout in a flipped view (y grows downward). Colors are semantic
NSColors so light/dark follow the system. The popover is rebuilt from the model
on every show or refresh - unlike menu items there is no in-place-mutation
constraint on a transient popover.
"""

from __future__ import annotations

import math
from collections.abc import Callable

import objc
from AppKit import (
    NSApplication,
    NSBox,
    NSButton,
    NSColor,
    NSFont,
    NSFontAttributeName,
    NSForegroundColorAttributeName,
    NSMakeRect,
    NSMutableAttributedString,
    NSPopover,
    NSTextField,
    NSView,
    NSViewController,
)
from Foundation import NSObject

from base_branch_watch.app.panel_model import PanelModel, PillTone, RepoTile
from base_branch_watch.core.models import Severity

PANEL_WIDTH = 440
PADDING = 14
GAP = 10
TILE_HEIGHT = 86
HEADER_HEIGHT = 30
FOOTER_HEIGHT = 34
TILE_WIDTH = (PANEL_WIDTH - 2 * PADDING - GAP) // 2

_NS_BOX_CUSTOM = 4
_NS_NO_BORDER = 0
_NS_LINE_BORDER = 1
_NS_NO_TITLE = 0
_NS_TRUNCATE_TAIL = 4
_NS_ALIGN_RIGHT = 1
_NS_MIN_Y_EDGE = 1
_NS_POPOVER_TRANSIENT = 1
_FONT_SEMIBOLD = 0.3
_FONT_BOLD = 0.4


class _FlippedView(NSView):
    def isFlipped(self):
        return True


class ActionTarget(NSObject):
    """One target for every button: the sender's tag indexes into the callback list."""

    def initWithCallbacks_(self, callbacks):
        self = objc.super(ActionTarget, self).init()
        if self is None:
            return None
        self._callbacks = callbacks
        return self

    @objc.IBAction
    def fire_(self, sender):
        tag = int(sender.tag())
        if 0 <= tag < len(self._callbacks):
            self._callbacks[tag](sender)


def _label(text, size, weight=None, color=None, mono=False, align=None):
    field = NSTextField.labelWithString_(text)
    if mono:
        field.setFont_(NSFont.monospacedSystemFontOfSize_weight_(size, 0.0))
    else:
        field.setFont_(NSFont.systemFontOfSize_weight_(size, weight or 0.0))
    field.setTextColor_(color or NSColor.labelColor())
    field.setLineBreakMode_(_NS_TRUNCATE_TAIL)
    if align is not None:
        field.setAlignment_(align)
    return field


def _place(view, parent, x, y, w, h):
    view.setFrame_(NSMakeRect(x, y, w, h))
    parent.addSubview_(view)
    return view


def _box(fill, radius, border=None, border_width=0.5):
    box = NSBox.alloc().init()
    box.setBoxType_(_NS_BOX_CUSTOM)
    box.setTitlePosition_(_NS_NO_TITLE)
    box.setContentViewMargins_((0, 0))
    box.setFillColor_(fill)
    box.setCornerRadius_(radius)
    if border is None:
        box.setBorderType_(_NS_NO_BORDER)
    else:
        box.setBorderType_(_NS_LINE_BORDER)
        box.setBorderColor_(border)
        box.setBorderWidth_(border_width)
    return box


def _dot(severity):
    color = NSColor.systemGreenColor() if severity == Severity.OK else NSColor.systemOrangeColor()
    return _box(color, 4.5)


def _pill_colors(tone):
    if tone == PillTone.OK:
        base = NSColor.systemGreenColor()
    elif tone == PillTone.ATTENTION:
        base = NSColor.systemOrangeColor()
    else:
        return NSColor.quaternaryLabelColor(), NSColor.secondaryLabelColor()
    return base.colorWithAlphaComponent_(0.18), base


def _arrows(behind, unpushed):
    text = NSMutableAttributedString.alloc().initWithString_(f"↓{behind}  ↑{unpushed}")
    font = NSFont.monospacedDigitSystemFontOfSize_weight_(11, _FONT_SEMIBOLD)
    behind_color = NSColor.systemOrangeColor() if behind else NSColor.tertiaryLabelColor()
    ahead_color = NSColor.systemBlueColor() if unpushed else NSColor.tertiaryLabelColor()
    split = len(f"↓{behind}")
    full = len(text.string())
    for start, end, color in ((0, split, behind_color), (split, full, ahead_color)):
        text.addAttributes_range_(
            {NSFontAttributeName: font, NSForegroundColorAttributeName: color},
            (start, end - start),
        )
    return text


def _tile_view(tile: RepoTile, tag: int, target) -> NSView:
    alert = tile.severity != Severity.OK
    border = NSColor.systemOrangeColor() if alert else NSColor.separatorColor()
    root = _FlippedView.alloc().initWithFrame_(NSMakeRect(0, 0, TILE_WIDTH, TILE_HEIGHT))
    # NSBox's own contentView is not flipped, so the box is only the background
    # and all content is laid out top-down in this flipped root.
    background = _box(NSColor.controlBackgroundColor(), 10, border, 1.5 if alert else 0.5)
    _place(background, root, 0, 0, TILE_WIDTH, TILE_HEIGHT)
    inner = root

    _place(_dot(tile.severity), inner, 11, 14, 9, 9)
    name = _label(tile.name, 13, _FONT_SEMIBOLD)
    _place(name, inner, 26, 9, TILE_WIDTH - 36, 17)
    base = _label(tile.base or " ", 10.5, color=NSColor.secondaryLabelColor(), mono=True)
    _place(base, inner, 26, 27, TILE_WIDTH - 36, 14)

    arrows = _label("", 11)
    arrows.setAttributedStringValue_(_arrows(tile.behind, tile.unpushed))
    _place(arrows, inner, 11, 46, 80, 14)
    detail = _label(tile.detail, 11, color=NSColor.secondaryLabelColor(), align=_NS_ALIGN_RIGHT)
    _place(detail, inner, 90, 46, TILE_WIDTH - 101, 14)

    fill, text_color = _pill_colors(tile.pill_tone)
    pill_label = _label(tile.pill, 10.5, _FONT_SEMIBOLD, text_color)
    pill_label.sizeToFit()
    pill_w = min(pill_label.frame().size.width + 14, TILE_WIDTH - 22)
    pill = _box(fill, 8)
    _place(pill, inner, 11, 64, pill_w, 16)
    _place(pill_label, pill.contentView(), 7, 1, pill_w - 14, 14)

    click = NSButton.alloc().init()
    click.setTransparent_(True)
    click.setTag_(tag)
    click.setTarget_(target)
    click.setAction_("fire:")
    _place(click, inner, 0, 0, TILE_WIDTH, TILE_HEIGHT)
    return root


def _checked_ago(seconds: float | None) -> str:
    if seconds is None:
        return "not checked yet"
    minutes = int(seconds // 60)
    return "checked just now" if minutes < 1 else f"checked {minutes}m ago"


class DashboardPanel:
    """Owns the NSPopover. `callbacks` are plain Python callables run on click."""

    def __init__(
        self,
        on_repo_click: Callable[[str], None],
        on_refresh: Callable[[], None],
        on_add_repo: Callable[[], None],
        on_more: Callable[[object], None],
    ) -> None:
        self._on_repo_click = on_repo_click
        self._on_more = on_more
        self._repo_paths: list[str] = []
        self._callbacks: list[Callable] = []
        self._target = ActionTarget.alloc().initWithCallbacks_(self._callbacks)
        self._popover = NSPopover.alloc().init()
        self._popover.setBehavior_(_NS_POPOVER_TRANSIENT)
        self._controller = NSViewController.alloc().init()
        self._popover.setContentViewController_(self._controller)
        self._actions = {"refresh": on_refresh, "add": on_add_repo}

    @property
    def is_shown(self) -> bool:
        return bool(self._popover.isShown())

    def close(self) -> None:
        self._popover.close()

    def update(self, model: PanelModel, seconds_since_check: float | None) -> None:
        """Rebuild the content view from `model` (cheap: a handful of tiles)."""
        self._callbacks.clear()
        self._repo_paths = [tile.repo_path for tile in model.tiles]
        for repo_path in self._repo_paths:
            self._callbacks.append(lambda _s, path=repo_path: self._on_repo_click(path))
        tile_count = len(self._callbacks)
        refresh_tag, add_tag, more_tag = (tile_count, tile_count + 1, tile_count + 2)
        self._callbacks.append(lambda _s: self._actions["refresh"]())
        self._callbacks.append(lambda _s: self._actions["add"]())
        self._callbacks.append(self._on_more)

        rows = math.ceil(tile_count / 2)
        grid_height = rows * TILE_HEIGHT + max(rows - 1, 0) * GAP
        height = PADDING + HEADER_HEIGHT + grid_height + GAP + FOOTER_HEIGHT + PADDING // 2
        root = _FlippedView.alloc().initWithFrame_(NSMakeRect(0, 0, PANEL_WIDTH, height))

        self._build_header(root, model, seconds_since_check)
        self._build_grid(root, model)
        footer_y = height - FOOTER_HEIGHT - PADDING // 2
        self._build_footer(root, footer_y, refresh_tag, add_tag, more_tag)

        self._controller.setView_(root)
        self._popover.setContentSize_((PANEL_WIDTH, height))

    def _build_header(self, root, model, seconds_since_check):
        title = f"{model.attention_count} need attention" if model.attention_count else "All clear"
        _place(_label(title, 15, _FONT_BOLD), root, PADDING, PADDING, 230, 20)
        sub = f"{model.clear_count} clear · {_checked_ago(seconds_since_check)}"
        subtitle = _label(sub, 11.5, color=NSColor.secondaryLabelColor(), align=_NS_ALIGN_RIGHT)
        _place(subtitle, root, PANEL_WIDTH - PADDING - 190, PADDING + 2, 190, 16)

    def _build_grid(self, root, model):
        top = PADDING + HEADER_HEIGHT
        for index, tile in enumerate(model.tiles):
            column, row = index % 2, index // 2
            x = PADDING + column * (TILE_WIDTH + GAP)
            y = top + row * (TILE_HEIGHT + GAP)
            _place(_tile_view(tile, index, self._target), root, x, y, TILE_WIDTH, TILE_HEIGHT)

    def _build_footer(self, root, y, refresh_tag, add_tag, more_tag):
        rule = _box(NSColor.separatorColor(), 0)
        _place(rule, root, PADDING, y, PANEL_WIDTH - 2 * PADDING, 1)
        for tag, title, x in (
            (refresh_tag, "Refresh", PADDING),
            (add_tag, "Add repo", PADDING + 70),
            (more_tag, "More…", PANEL_WIDTH - PADDING - 60),
        ):
            button = NSButton.buttonWithTitle_target_action_(title, self._target, "fire:")
            button.setBordered_(False)
            button.setTag_(tag)
            button.setContentTintColor_(NSColor.controlAccentColor())
            _place(button, root, x, y + 6, 66, 22)

    def show(self, anchor_view) -> None:
        """Show relative to the status-item button; toggles closed if already open."""
        if self.is_shown:
            self._popover.close()
            return
        # An accessory (menu bar only) app is never active on its own; without this
        # the transient popover can dismiss itself as soon as it opens.
        NSApplication.sharedApplication().activateIgnoringOtherApps_(True)
        self._popover.showRelativeToRect_ofView_preferredEdge_(
            anchor_view.bounds(), anchor_view, _NS_MIN_Y_EDGE
        )

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
    NSBox,
    NSButton,
    NSColor,
    NSFont,
    NSFontAttributeName,
    NSForegroundColorAttributeName,
    NSMakeRect,
    NSMutableAttributedString,
    NSPopover,
    NSScrollView,
    NSSegmentedControl,
    NSTextField,
    NSView,
    NSViewController,
    NSWindowCollectionBehaviorCanJoinAllSpaces,
    NSWindowCollectionBehaviorFullScreenAuxiliary,
)
from Foundation import NSObject

from base_branch_watch.app.panel_model import (
    PanelModel,
    PillTone,
    PrRow,
    RepoTile,
    StaleGroup,
)
from base_branch_watch.core.models import Severity

PANEL_WIDTH = 520
PADDING = 20
GAP = 14
TILE_HEIGHT = 116
HEADER_HEIGHT = 44
FOOTER_HEIGHT = 44
TABS_HEIGHT = 30
MAX_CONTENT_HEIGHT = 470
CONTENT_WIDTH = PANEL_WIDTH - 2 * PADDING
TILE_WIDTH = (CONTENT_WIDTH - GAP) // 2
TILE_WIDTH_PAD = 14
STALE_TILE_HEIGHT = 150
STALE_PREVIEW_ROWS = 3
TAB_TITLES = ("Repos", "PRs", "Stale")

_NS_BOX_CUSTOM = 4
_NS_NO_BORDER = 0
_NS_LINE_BORDER = 1
_NS_NO_TITLE = 0
_NS_TRUNCATE_TAIL = 4
_NS_ALIGN_CENTER = 1
_NS_ALIGN_RIGHT = 2
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
    font = NSFont.monospacedDigitSystemFontOfSize_weight_(14, _FONT_SEMIBOLD)
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

    text_x = TILE_WIDTH_PAD + 18
    _place(_dot(tile.severity), inner, TILE_WIDTH_PAD, 19, 10, 10)
    name = _label(tile.name, 16, _FONT_SEMIBOLD)
    _place(name, inner, text_x, 14, TILE_WIDTH - text_x - TILE_WIDTH_PAD, 21)
    base = _label(tile.base or " ", 12.5, color=NSColor.secondaryLabelColor(), mono=True)
    _place(base, inner, text_x, 38, TILE_WIDTH - text_x - TILE_WIDTH_PAD, 17)

    arrows = _label("", 14)
    arrows.setAttributedStringValue_(_arrows(tile.behind, tile.unpushed))
    _place(arrows, inner, TILE_WIDTH_PAD, 64, 100, 18)
    detail = _label(tile.detail, 13, color=NSColor.secondaryLabelColor(), align=_NS_ALIGN_RIGHT)
    _place(detail, inner, 110, 64, TILE_WIDTH - 110 - TILE_WIDTH_PAD, 18)

    fill, text_color = _pill_colors(tile.pill_tone)
    pill_label = _label(tile.pill, 12.5, _FONT_SEMIBOLD, text_color)
    pill_label.sizeToFit()
    pill_w = min(pill_label.frame().size.width + 20, TILE_WIDTH - 2 * TILE_WIDTH_PAD)
    pill = _box(fill, 10)
    _place(pill, inner, TILE_WIDTH_PAD, 88, pill_w, 20)
    _place(pill_label, pill.contentView(), 10, 1, pill_w - 20, 17)

    click = NSButton.alloc().init()
    click.setTransparent_(True)
    click.setTag_(tag)
    click.setTarget_(target)
    click.setAction_("fire:")
    _place(click, inner, 0, 0, TILE_WIDTH, TILE_HEIGHT)
    return root


def _card(width, height, alert=False):
    """A flipped container with a rounded NSBox background (NSBox's own contentView
    is not flipped, so content is laid out top-down in this root instead)."""
    root = _FlippedView.alloc().initWithFrame_(NSMakeRect(0, 0, width, height))
    border = NSColor.systemOrangeColor() if alert else NSColor.separatorColor()
    background = _box(NSColor.controlBackgroundColor(), 10, border, 1.5 if alert else 0.5)
    _place(background, root, 0, 0, width, height)
    return root


def _pill(parent, text, tone, x, y, max_width):
    fill, text_color = _pill_colors(tone)
    label = _label(text, 12.5, _FONT_SEMIBOLD, text_color)
    label.sizeToFit()
    width = min(label.frame().size.width + 20, max_width)
    pill = _box(fill, 10)
    _place(pill, parent, x, y, width, 20)
    _place(label, pill.contentView(), 10, 1, width - 20, 17)
    return width


def _clickable(parent, tag, target, width, height):
    click = NSButton.alloc().init()
    click.setTransparent_(True)
    click.setTag_(tag)
    click.setTarget_(target)
    click.setAction_("fire:")
    _place(click, parent, 0, 0, width, height)


def _tone_dot(tone: PillTone) -> NSView:
    colors = {
        PillTone.OK: NSColor.systemGreenColor(),
        PillTone.ATTENTION: NSColor.systemOrangeColor(),
        PillTone.NEUTRAL: NSColor.tertiaryLabelColor(),
    }
    return _box(colors[tone], 5)


def _pr_tile(row: PrRow, tag: int, target) -> NSView:
    """Same shape as a repo tile: dot, name, branch, one detail row, one pill."""
    alert = row.pill_tone == PillTone.ATTENTION
    card = _card(TILE_WIDTH, TILE_HEIGHT, alert)
    pad = TILE_WIDTH_PAD
    text_x = pad + 18
    inner_w = TILE_WIDTH - text_x - pad
    _place(_tone_dot(row.pill_tone), card, pad, 19, 10, 10)
    _place(_label(row.repo_name, 16, _FONT_SEMIBOLD), card, text_x, 14, inner_w, 21)
    branch = _label(row.branch or " ", 12.5, color=NSColor.secondaryLabelColor(), mono=True)
    _place(branch, card, text_x, 38, inner_w, 17)

    number = f"PR #{row.number}" if row.number is not None else " "
    _place(_label(number, 14, _FONT_SEMIBOLD), card, pad, 64, 90, 18)
    review = _label(row.review, 13, color=NSColor.secondaryLabelColor(), align=_NS_ALIGN_RIGHT)
    _place(review, card, 100, 64, TILE_WIDTH - 100 - pad, 18)
    _pill(card, row.pill, row.pill_tone, pad, 88, TILE_WIDTH - 2 * pad)
    _clickable(card, tag, target, TILE_WIDTH, TILE_HEIGHT)
    return card


def _stale_tile(group: StaleGroup, tag: int, target) -> NSView:
    """Same shape as a repo tile, taller to preview the first few branch names."""
    needs_review = any(branch.needs_review for branch in group.branches)
    card = _card(TILE_WIDTH, STALE_TILE_HEIGHT, needs_review)
    pad = TILE_WIDTH_PAD
    text_x = pad + 18
    inner_w = TILE_WIDTH - text_x - pad
    tone = PillTone.ATTENTION if needs_review else PillTone.NEUTRAL
    _place(_tone_dot(tone), card, pad, 19, 10, 10)
    _place(_label(group.repo_name, 16, _FONT_SEMIBOLD), card, text_x, 14, inner_w, 21)
    count = len(group.branches)
    summary = f"{count} stale branch{'' if count == 1 else 'es'}"
    _place(_label(summary, 13, color=NSColor.secondaryLabelColor()), card, text_x, 38, inner_w, 18)

    # Branches that need a look (remote gone, not merged) sort first.
    ordered = sorted(group.branches, key=lambda branch: not branch.needs_review)
    for index, branch in enumerate(ordered[:STALE_PREVIEW_ROWS]):
        color = NSColor.systemOrangeColor() if branch.needs_review else NSColor.labelColor()
        name = _label(branch.name, 12.5, color=color, mono=True)
        _place(name, card, pad, 66 + index * 20, TILE_WIDTH - 2 * pad, 17)
    hidden = count - STALE_PREVIEW_ROWS
    if hidden > 0:
        more = _label(f"+{hidden} more", 12.5, color=NSColor.secondaryLabelColor())
        _place(more, card, pad, 66 + STALE_PREVIEW_ROWS * 20, TILE_WIDTH - 2 * pad, 17)
    _clickable(card, tag, target, TILE_WIDTH, STALE_TILE_HEIGHT)
    return card


def _empty_state(text: str) -> NSView:
    view = _FlippedView.alloc().initWithFrame_(NSMakeRect(0, 0, CONTENT_WIDTH, 120))
    label = _label(text, 14, color=NSColor.secondaryLabelColor(), align=_NS_ALIGN_CENTER)
    _place(label, view, 0, 48, CONTENT_WIDTH, 20)
    return view


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
        on_stale_click: Callable[[str], None],
        on_refresh: Callable[[], None],
        on_add_repo: Callable[[], None],
    ) -> None:
        self._on_repo_click = on_repo_click
        self._on_stale_click = on_stale_click
        self._on_refresh = on_refresh
        self._on_add_repo = on_add_repo
        self._callbacks: list[Callable] = []
        self._target = ActionTarget.alloc().initWithCallbacks_(self._callbacks)
        self._popover = NSPopover.alloc().init()
        self._popover.setBehavior_(_NS_POPOVER_TRANSIENT)
        self._controller = NSViewController.alloc().init()
        self._popover.setContentViewController_(self._controller)
        self._tab = 0
        self._model: PanelModel | None = None
        self._seconds_since_check: float | None = None

    @property
    def is_shown(self) -> bool:
        return bool(self._popover.isShown())

    def close(self) -> None:
        self._popover.close()

    def update(self, model: PanelModel, seconds_since_check: float | None) -> None:
        """Rebuild the content from `model` (cheap: a handful of tiles and rows)."""
        self._model = model
        self._seconds_since_check = seconds_since_check
        self._rebuild()

    def _register(self, callback: Callable) -> int:
        self._callbacks.append(callback)
        return len(self._callbacks) - 1

    def _select_tab(self, sender) -> None:
        self._tab = int(sender.selectedSegment())
        self._rebuild()

    def _rebuild(self) -> None:
        model = self._model
        if model is None:
            return
        self._callbacks.clear()
        content, content_height = self._build_content(model)
        viewport = min(content_height, MAX_CONTENT_HEIGHT)
        top = PADDING + HEADER_HEIGHT + TABS_HEIGHT + GAP
        height = top + viewport + GAP + FOOTER_HEIGHT + PADDING // 2
        root = _FlippedView.alloc().initWithFrame_(NSMakeRect(0, 0, PANEL_WIDTH, height))

        self._build_header(root, model)
        self._build_tabs(root, model)
        self._place_scrolling(root, content, content_height, top, viewport)
        self._build_footer(root, height - FOOTER_HEIGHT - PADDING // 2)

        self._controller.setView_(root)
        self._popover.setContentSize_((PANEL_WIDTH, height))

    def _build_content(self, model: PanelModel) -> tuple[NSView, int]:
        if self._tab == 1:
            return self._build_prs(model)
        if self._tab == 2:
            return self._build_stale(model)
        return self._build_grid(model)

    def _build_grid(self, model: PanelModel) -> tuple[NSView, int]:
        if not model.tiles:
            return _empty_state("No repos watched yet"), 120
        rows = math.ceil(len(model.tiles) / 2)
        height = rows * TILE_HEIGHT + (rows - 1) * GAP
        grid = _FlippedView.alloc().initWithFrame_(NSMakeRect(0, 0, CONTENT_WIDTH, height))
        for index, tile in enumerate(model.tiles):
            repo_path = tile.repo_path
            tag = self._register(lambda _s, path=repo_path: self._on_repo_click(path))
            x = (index % 2) * (TILE_WIDTH + GAP)
            y = (index // 2) * (TILE_HEIGHT + GAP)
            _place(_tile_view(tile, tag, self._target), grid, x, y, TILE_WIDTH, TILE_HEIGHT)
        return grid, height

    def _build_tile_grid(self, views: list[NSView], tile_height: int) -> tuple[NSView, int]:
        rows = math.ceil(len(views) / 2)
        height = rows * tile_height + (rows - 1) * GAP
        grid = _FlippedView.alloc().initWithFrame_(NSMakeRect(0, 0, CONTENT_WIDTH, height))
        for index, view in enumerate(views):
            x = (index % 2) * (TILE_WIDTH + GAP)
            y = (index // 2) * (tile_height + GAP)
            _place(view, grid, x, y, TILE_WIDTH, tile_height)
        return grid, height

    def _build_prs(self, model: PanelModel) -> tuple[NSView, int]:
        if not model.prs:
            return _empty_state("No pull requests on watched branches"), 120
        views = []
        for row in model.prs:
            repo_path = row.repo_path
            tag = self._register(lambda _s, path=repo_path: self._on_repo_click(path))
            views.append(_pr_tile(row, tag, self._target))
        return self._build_tile_grid(views, TILE_HEIGHT)

    def _build_stale(self, model: PanelModel) -> tuple[NSView, int]:
        if not model.stale_groups:
            return _empty_state("No stale branches"), 120
        views = []
        for group in model.stale_groups:
            repo_path = group.repo_path
            tag = self._register(lambda _s, path=repo_path: self._on_stale_click(path))
            views.append(_stale_tile(group, tag, self._target))
        return self._build_tile_grid(views, STALE_TILE_HEIGHT)

    def _build_header(self, root, model: PanelModel) -> None:
        title = f"{model.attention_count} need attention" if model.attention_count else "All clear"
        _place(_label(title, 20, _FONT_BOLD), root, PADDING, PADDING, 280, 26)
        sub = f"{model.clear_count} clear · {_checked_ago(self._seconds_since_check)}"
        subtitle = _label(sub, 13.5, color=NSColor.secondaryLabelColor(), align=_NS_ALIGN_RIGHT)
        _place(subtitle, root, PANEL_WIDTH - PADDING - 220, PADDING + 5, 220, 18)

    def _build_tabs(self, root, model: PanelModel) -> None:
        counts = (len(model.tiles), len(model.prs), model.stale_total)
        tabs = NSSegmentedControl.alloc().initWithFrame_(NSMakeRect(0, 0, CONTENT_WIDTH, 28))
        tabs.setSegmentCount_(len(TAB_TITLES))
        for index, (title, count) in enumerate(zip(TAB_TITLES, counts)):
            tabs.setLabel_forSegment_(f"{title}  {count}", index)
        tabs.setSelectedSegment_(self._tab)
        tabs.setFont_(NSFont.systemFontOfSize_weight_(13, _FONT_SEMIBOLD))
        tabs.setTag_(self._register(self._select_tab))
        tabs.setTarget_(self._target)
        tabs.setAction_("fire:")
        _place(tabs, root, PADDING, PADDING + HEADER_HEIGHT, CONTENT_WIDTH, 28)

    @staticmethod
    def _place_scrolling(root, content, content_height, top, viewport) -> None:
        scroll = NSScrollView.alloc().initWithFrame_(
            NSMakeRect(PADDING, top, CONTENT_WIDTH, viewport)
        )
        scroll.setHasVerticalScroller_(True)
        scroll.setAutohidesScrollers_(True)
        scroll.setDrawsBackground_(False)
        scroll.setBorderType_(_NS_NO_BORDER)
        content.setFrame_(NSMakeRect(0, 0, CONTENT_WIDTH, content_height))
        scroll.setDocumentView_(content)
        root.addSubview_(scroll)

    def _build_footer(self, root, y: float) -> None:
        rule = _box(NSColor.separatorColor(), 0)
        _place(rule, root, PADDING, y, CONTENT_WIDTH, 1)
        for title, callback, x in (
            ("Refresh", lambda _s: self._on_refresh(), PADDING),
            ("Add repo", lambda _s: self._on_add_repo(), PADDING + 96),
        ):
            button = NSButton.buttonWithTitle_target_action_(title, self._target, "fire:")
            button.setBordered_(False)
            button.setFont_(NSFont.systemFontOfSize_weight_(14, _FONT_SEMIBOLD))
            button.setTag_(self._register(callback))
            button.setContentTintColor_(NSColor.controlAccentColor())
            _place(button, root, x, y + 10, 90, 26)

    def show(self, anchor_view) -> None:
        """Show relative to the status-item button; toggles closed if already open."""
        if self.is_shown:
            self._popover.close()
            return
        self._popover.showRelativeToRect_ofView_preferredEdge_(
            anchor_view.bounds(), anchor_view, _NS_MIN_Y_EDGE
        )
        self._follow_active_space()

    def _follow_active_space(self) -> None:
        """Let the popover appear over whatever space or full-screen app is in front.

        Without this the window belongs to the desktop space, so clicking the icon
        while another app is full screen opens nothing visible there (and activating
        the app instead would yank the user back to the desktop).
        """
        window = self._controller.view().window()
        if window is not None:
            window.setCollectionBehavior_(
                NSWindowCollectionBehaviorCanJoinAllSpaces
                | NSWindowCollectionBehaviorFullScreenAuxiliary
            )

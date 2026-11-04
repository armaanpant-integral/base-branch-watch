"""DashboardPanel tile buttons: real AppKit views, callbacks fired through the tag registry."""

from __future__ import annotations

from types import SimpleNamespace

from AppKit import NSButton

from base_branch_watch.app import panel_view
from base_branch_watch.app.panel_model import PanelModel, PillTone, RepoTile
from base_branch_watch.core.models import Severity


def _tile(name: str) -> RepoTile:
    return RepoTile(
        repo_path=f"/repos/{name}",
        name=name,
        base="main",
        behind=0,
        unpushed=0,
        severity=Severity.OK,
        pill="No open PR",
        pill_tone=PillTone.NEUTRAL,
        detail="",
    )


def _panel(calls: list[tuple[str, str]]) -> panel_view.DashboardPanel:
    panel = panel_view.DashboardPanel(
        on_repo_click=lambda path: calls.append(("open", path)),
        on_stale_click=lambda path: calls.append(("stale", path)),
        on_refresh=lambda: calls.append(("refresh", "")),
        on_add_repo=lambda: calls.append(("add", "")),
        on_edit_base=lambda path: calls.append(("edit", path)),
        on_remove_repo=lambda path: calls.append(("remove", path)),
    )
    panel.update(PanelModel(tiles=(_tile("alpha"), _tile("beta")), prs=(), stale_groups=()), 1.0)
    return panel


def _buttons(view) -> list[NSButton]:
    found = []
    for sub in view.subviews():
        if isinstance(sub, NSButton):
            found.append(sub)
        found.extend(_buttons(sub))
    return found


def _by_tooltip(panel: panel_view.DashboardPanel, tooltip: str) -> list[NSButton]:
    return [b for b in _buttons(panel._controller.view()) if b.toolTip() == tooltip]


def test_each_tile_pencil_edits_its_own_repo_not_remove():
    calls: list[tuple[str, str]] = []
    panel = _panel(calls)

    for button in _by_tooltip(panel, "Edit base branch"):
        panel._target.fire_(button)

    assert sorted(calls) == [("edit", "/repos/alpha"), ("edit", "/repos/beta")]


def test_each_tile_cross_removes_its_own_repo_not_edit():
    calls: list[tuple[str, str]] = []
    panel = _panel(calls)

    for button in _by_tooltip(panel, "Remove repo"):
        panel._target.fire_(button)

    assert sorted(calls) == [("remove", "/repos/alpha"), ("remove", "/repos/beta")]


def test_icon_buttons_have_distinct_tags_from_the_tile_click():
    panel = _panel([])
    tags = [b.tag() for b in _buttons(panel._controller.view())]

    assert len(tags) == len(set(tags))


def test_icon_buttons_sit_above_the_full_tile_click_target():
    panel = _panel([])
    tile = _by_tooltip(panel, "Remove repo")[0].superview()
    order = [sub for sub in tile.subviews() if isinstance(sub, NSButton)]

    # Last-added subview is frontmost, so the icons must come after the tile-wide click.
    assert order[0].toolTip() is None
    assert {b.toolTip() for b in order[1:]} == {"Remove repo", "Edit base branch"}


def test_icon_falls_back_to_a_text_glyph_when_the_symbol_is_missing(monkeypatch):
    # NSImage is an ObjC class (selectors can't be patched), so swap the module's reference.
    missing_symbol = SimpleNamespace(
        imageWithSystemSymbolName_accessibilityDescription_=lambda *_: None
    )
    monkeypatch.setattr(panel_view, "NSImage", missing_symbol)

    button = panel_view._tile_action("nope", "✕", "Remove repo", 3, target=None)

    assert button.title() == "✕"
    assert button.image() is None

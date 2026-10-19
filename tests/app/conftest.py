"""App tests construct a real BaseBranchWatchApp; keep its self-update check
off the real checkout (it would `git fetch` this repo) unless a test opts in."""

from __future__ import annotations

import pytest

from base_branch_watch.app import menubar


@pytest.fixture(autouse=True)
def _no_real_self_update_check(monkeypatch):
    monkeypatch.setattr(menubar.self_update, "checkout_root", lambda *a, **k: None)

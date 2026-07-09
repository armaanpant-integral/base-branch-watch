"""`python -m base_branch_watch` entry point — launches the menu-bar app."""

from __future__ import annotations

from base_branch_watch.app.menubar import BaseBranchWatchApp


def main() -> None:
    BaseBranchWatchApp().run()


if __name__ == "__main__":
    main()

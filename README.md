# base-branch-watch

A macOS menu-bar companion that watches your repos' base branches in the background and tells you when they've moved ahead of you — no more manually remembering to `git fetch`.

## What it does

- Watches any number of repos, each with one or more base branches (e.g. `main`, or a team integration branch)
- Menu bar shows live status per repo: up to date / behind / unpushed / diverged
- Multi-base repos expand into a real submenu, one row per base branch
- One consolidated notification per polling cycle (not spammy per-repo popups), deduped so you're not renotified for the same change
- Parallel, bounded-concurrency fetches — adding more repos doesn't slow down each cycle
- Runs as a `launchd` LaunchAgent: starts at login, survives crashes

Not yet built (see [Roadmap](#roadmap)): conflict-risk warnings, a pre-push gate, PR status in the menu.

## Install

```bash
git clone https://github.com/armaanpant-integral/base-branch-watch.git
cd base-branch-watch
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

Then install it as a LaunchAgent so it runs continuously and survives login/logout:

```bash
python -m base_branch_watch.cli install-agent "$(pwd)/.venv/bin/python3"
```

> **Known issue:** don't rely on the `bbwatch` console-script shortcut yet — it currently fails with `ModuleNotFoundError` even on a correct install. Use `python -m base_branch_watch.cli <command>` instead.
>
> **Also don't omit the interpreter path above.** The installer's auto-detect (`command -v python3`) can pick your system Python instead of this venv, producing a LaunchAgent that silently fails at every login. Always pass your venv's interpreter path explicitly, as shown.

Full setup details, macOS Full Disk Access / notification-permission gotchas, and uninstall steps: see [INSTALL.md](INSTALL.md).

## Using it

Click the menu bar icon to see watched repos and their status. From the menu:

- **Add Repo…** — pick a repo folder; base branch is auto-detected from `origin`'s default, editable
- **Edit Base Branch(es)** — change a repo's watched base branch(es) without removing and re-adding it
- **Remove Repo**
- **Set Interval…** — change the polling interval (default 300s)
- **Refresh Now**
- **Open Log** — today's run log, rotated daily

## Development

```bash
source .venv/bin/activate
pytest tests/ -q       # 83 tests
ruff check .
```

Built test-first (TDD) with a UI-free `core/` library — see [CLAUDE.md](CLAUDE.md) for architecture, stack rationale, and conventions.

## Roadmap

Currently: **Phase 1 (Modular Status Monitor)** — shipped, tested, running.

Next:
- **Phase 2** — ambient conflict-risk heuristic: warn when incoming base-branch changes overlap files you've touched, before you even try to push
- **Phase 3** — pre-push gate: a `git push` hook that always shows what changed on the base branch, and escalates to an interactive prompt only when `git merge-tree` finds a real conflict
- **Phase 4** — PR status (checks/review/mergeable state) in the menu, via `gh`

## License

Personal project, no license file yet — ask before reusing outside personal/internal use.

# Installing base-branch-watch

This app runs as a `launchd` LaunchAgent so it starts automatically at login
and survives crashes (`KeepAlive`). This document covers install, uninstall,
and the one operational gotcha (macOS Full Disk Access) that isn't something
the code can handle for you.

## Prerequisites

- **Python 3.11+** (3.12 recommended — see the project's stack notes; system
  Python on macOS is frequently older and EOL, don't assume `python3` on
  `PATH` is new enough without checking `python3 --version`).
- **git 2.38+** — required for `git merge-tree --write-tree` (used by the
  conflict-risk feature). Check with `git --version`.
- The `base_branch_watch` package installed into the interpreter you intend
  to bake into the LaunchAgent, e.g. from a repo checkout:

  ```sh
  python3 -m venv .venv
  source .venv/bin/activate
  pip install -e .
  ```

## Install

Run the install script, passing the interpreter that has `base_branch_watch`
installed (typically your venv's `python3`):

```sh
scripts/install-launchagent.sh /path/to/.venv/bin/python3
```

If you omit the interpreter argument, the script resolves `python3` via
`command -v python3` and prints which interpreter it baked in — read that
line and confirm it's the one you expect before trusting the install.

The script:

1. Resolves and validates the interpreter (`import base_branch_watch` must
   succeed against it — the script fails loudly if not, rather than
   installing a LaunchAgent that will crash-loop on every launchd respawn).
2. Renders `scripts/com.armaan.base-branch-watch.plist.template` with that
   absolute interpreter path (never a `PATH`-resolved shebang — see the
   "why absolute path" note below) and writes the result to
   `~/Library/LaunchAgents/com.armaan.base-branch-watch.plist`.
3. Loads it via the modern `launchctl bootstrap gui/$(id -u)` +
   `launchctl enable` pair (the legacy `launchctl load` is deprecated).

### Confirm it's running

```sh
launchctl list | grep base-branch-watch
```

A row with a PID (not `-`) means the app is running. The menu bar icon
should also appear.

### Why an absolute interpreter path, not `PATH`-resolved

`launchd` invokes agents without sourcing your shell profile or activating
any virtualenv — a `#!/usr/bin/env python3`-style resolution would run
whatever `python3` happens to be first on the *login environment's* bare
`PATH`, which is not guaranteed to be the interpreter you installed the
package into. Baking in the absolute path at install time avoids this
entirely (same reasoning as the project's git-hook installer).

## Uninstall

```sh
scripts/install-launchagent.sh --uninstall
```

This unloads the agent (`launchctl bootout`) and removes the installed
plist. It does **not** touch `config.json` or `state.json` — your watched
repo list and notification-dedupe state are left alone in case you
reinstall later.

## `bbwatch` CLI shortcut

The same install/uninstall logic is also reachable via the `bbwatch`
console script (installed by `pip install -e .`), which just shells out to
`scripts/install-launchagent.sh`:

```sh
bbwatch install-agent [/path/to/python3]
bbwatch uninstall-agent
```

## KeepAlive vs. Quit — important caveat

`KeepAlive: true` means launchd respawns the process whenever it exits —
including a crash, and including `kill`. **This means the app's in-menu
`Quit` item does not durably stop it**; launchd will start it right back up.
The only way to actually stop the app is to unload the LaunchAgent:

```sh
launchctl bootout gui/$(id -u)/com.armaan.base-branch-watch
```

(or re-run `scripts/install-launchagent.sh --uninstall`, which does the same
thing plus removes the plist). This is intentional behavior (crash-respawn
is the whole point of `KeepAlive`), not a bug — don't be surprised when
"Quit" doesn't stick.

## Full Disk Access (TCC)

macOS's TCC (Transparency, Consent, Control) privacy system gates access to
protected folders (Desktop, Documents, Downloads, and some others) **per
executable**, not per user. If any of your watched repos live under one of
these folders, `git fetch`/`git status` calls made by the interpreter
launchd spawns may silently fail or hang until that exact interpreter binary
is granted Full Disk Access.

1. Open **System Settings → Privacy & Security → Full Disk Access**.
2. Click **+** and add the exact interpreter binary baked into the
   LaunchAgent (the path the install script printed as "Baking in
   interpreter: ...") — not a wrapper script, not `/usr/bin/python3` if you
   actually installed into a venv's interpreter.
3. **TCC grants only take effect on process restart, not dynamically** —
   after granting access, reload the agent so the new grant is picked up:

   ```sh
   launchctl bootout gui/$(id -u)/com.armaan.base-branch-watch
   scripts/install-launchagent.sh /path/to/.venv/bin/python3
   ```

This is a one-time, per-interpreter operational step — the app itself
cannot request or verify this grant programmatically; it's called out here
so it isn't re-discovered as a mystery bug (it has already surprised this
project's author more than once with the earlier bash-script prototype).

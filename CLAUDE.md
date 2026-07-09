<!-- GSD:project-start source:PROJECT.md -->
## Project

**base-branch-watch**

A macOS menu-bar companion app that eliminates "I forgot to `git pull`" and catches merge conflicts before they happen. It watches configured repos' base branches in the background, surfaces drift in the menu bar, and gates every `git push` with a conflict-risk check and a summary of what changed on the base branch since you last synced. Built for a single lazy dev first (Armaan), architected so it can later be handed to colleagues without a rewrite.

**Core Value:** Never manually fetch or check a base branch again, and never get surprised by a merge conflict in a PR — code in peace, get told what changed and whether it conflicts right before you push.

### Constraints

- **Platform**: macOS only — `rumps`/PyObjC for the menu bar UI, `launchd` for scheduling/autostart, `osascript` for notifications. No cross-platform requirement for v1.
- **Notification mechanism note:** `osascript display notification` has no distinct app identity, so clicking a notification activates whatever process ran it (commonly "Script Editor") instead of showing anything useful — a known, accepted cosmetic limitation of v1. `terminal-notifier` was evaluated as a fix and rejected: it's unmaintained since 2017 and confirmed non-functional on macOS 26 in manual testing (no permission prompt, no notification registered, silent no-op). Stick with `osascript` for v1. The only mechanism confirmed to properly fix app identity is `UNUserNotificationCenter` via `pyobjc-framework-UserNotifications`, which requires the signed `.app` bundle already planned for v2 — see Packaging section.
- **No new external services or credentials**: must work with what's already authenticated locally (SSH keys via `ssh-agent`, `gh auth login`) — no new API keys, no hosted backend, no message-queue/webhook infrastructure.
- **No signed app bundle for v1**: sticking with a plain Python script run via `python3`, not a compiled/signed `.app`. This is *why* actionable notification buttons are out of scope for v1 — they need the bundle infrastructure this constraint excludes.
- **Personal-first architecture**: single user, single machine for v1 — but code should avoid hardcoding assumptions (e.g. absolute personal paths baked into logic rather than config) that would make v2 sharing a rewrite rather than a packaging exercise.
<!-- GSD:project-end -->

<!-- GSD:stack-start source:research/STACK.md -->
## Technology Stack

## Recommended Stack
### Core Technologies
| Technology | Version | Purpose | Why Recommended |
|------------|---------|---------|-----------------|
| Python | 3.11 or 3.12 | Runtime | Keep — matches prototype language, best fit for git-shelling/JSON/text-processing glue code. Do not stay on 3.9 (prototype's version): 3.9 is EOL Oct 2025. Target 3.11+ for perf and `tomllib`; avoid bleeding-edge 3.13 until `py2app`'s 3.13 support is confirmed stable in your own build (see Packaging below). |
| `rumps` | 0.4.0 (latest PyPI) | Menu bar UI + app lifecycle (NSStatusBar wrapper over PyObjC) | **Keep.** Still the standard, lightest way to get a native `NSStatusItem` menu bar app in Python. It is a thin, largely stable wrapper — low commit velocity is normal for a "done" utility library, not abandonment; API surface (menu items, timers, `rumps.notification`) hasn't needed to change because `NSStatusBar` itself hasn't changed. No credible Python-ecosystem successor has emerged. HIGH confidence this is still correct for a Python-based menu bar app. |
| PyObjC (`pyobjc-core`, `pyobjc-framework-Cocoa`) | latest (rumps dependency) | Bridge to AppKit/Cocoa | Keep — pulled in transitively by `rumps`. Also your path to `UNUserNotificationCenter` later (see below) without a Swift rewrite. |
| `git` (system binary) | 2.38+ | All git plumbing operations | Keep subprocess-based git usage (see rationale under "shell out vs library" below). Pin your minimum supported git version to 2.38 — this is the first version where `git merge-tree --write-tree` (the non-mutating conflict-detection mode you're planning) exists. Detect and hard-fail-with-message if the user's system git predates it. |
| `gh` CLI | 2.90+ (latest stable, actively released weekly-ish; verify at install time, don't pin an exact patch) | PR/CI status | Keep — reuses existing `gh auth login`, no new credentials, exactly matches the constraint in PROJECT.md. |
### Supporting Libraries
| Library | Version | Purpose | When to Use |
|---------|---------|---------|-------------|
| `concurrent.futures.ThreadPoolExecutor` (stdlib) | Python 3.11+ stdlib | Parallelize per-repo `git fetch`/`git ls-remote` calls | Use for the "batched, not sequential" polling requirement in PROJECT.md. Each `subprocess.run(["git", "fetch", ...])` call is I/O-bound (network + process spawn), so a thread pool (not `multiprocessing`) is the right primitive — no GIL contention because the work happens in the subprocess, not in Python bytecode. Cap workers (e.g. `min(8, len(repos))`) to avoid hammering git remotes/SSH agent concurrently. |
| `json` (stdlib) | stdlib | Config + per-repo notification state persistence | Keep — no reason to introduce SQLite or a config library for a handful of small, human-editable JSON files. If config schema grows complex, consider `pydantic` for validation, not a different storage format. |
| `pyobjc-framework-UserNotifications` | latest (matches your `pyobjc-core` version) | Access to `UNUserNotificationCenter` (actionable notification buttons) | **Not needed for v1** (constraint explicitly excludes signed bundle / actionable notifications). Add when you build the v2 packaged path — see Packaging section. This is the key finding that changes your v2 plan: you do **not** need a Swift rewrite to get `UNUserNotificationCenter`; PyObjC has first-class bindings for it, gated only on the app being code-signed. |
| `subprocess` (stdlib) | stdlib | All git/`gh`/`osascript` invocations | Keep. Always pass argument lists (never `shell=True`), always set `cwd` explicitly per repo, always set a `timeout=` (network calls to unreachable remotes will otherwise hang the poller). |
### Development Tools
| Tool | Purpose | Notes |
|------|---------|-------|
| `py2app` | v1-adjacent bundling tool, needed only when you build the signed `.app` for v2 | See Packaging section — recommended over Briefcase for this specific app shape. |
| `pytest` | Test runner | Standard choice; mock `subprocess.run` calls to test git-output-parsing logic (e.g. `merge-tree` conflict parsing) without needing real repos in CI. |
| `ruff` | Lint + format | Modern standard replacing `flake8`+`black`+`isort` combo; single fast binary, minimal config. |
## Installation
# Core (v1, unsigned script — matches current constraint)
# When ready to build the v2 signed .app bundle
# Dev dependencies
## Alternatives Considered
| Recommended | Alternative | When to Use Alternative |
|-------------|-------------|--------------------------|
| `rumps`/PyObjC | Swift/SwiftUI `MenuBarExtra` | Use if you decide to fully rewrite for v2 distribution to colleagues *and* want the most idiomatic, lowest-friction path to code signing, notarization, and Xcode-managed entitlements. The tradeoff: your git-shelling, JSON parsing, and `merge-tree` output parsing all have to be reimplemented in Swift (`Process`, `Codable`, string parsing) — a real rewrite, not a port. Given this is a solo/personal-first v1 with plans to formalize later, don't pay this cost now. Revisit only if v2 colleague-distribution becomes a hard requirement and the actionable-notification-button feature (currently explicitly out of scope) becomes must-have. |
| `rumps`/PyObjC | Electron + `tray` API (e.g. `electron-builder` menu bar template) | Never, for this app. Electron bundles a full Chromium/Node runtime (100+ MB) for what is fundamentally a text-status menu bar icon with no complex UI needs. This is the wrong tool even for eventual colleague distribution — a bigger download, slower cold start, higher memory footprint, and no meaningful DX win over Python here since you're not building rich UI. |
| `subprocess` + system `git` | `pygit2` (libgit2 bindings) | Use if you need programmatic access to git internals at high call volume where process-spawn overhead genuinely matters (e.g. hundreds of ops/sec), or you need merge/diff APIs libgit2 exposes more conveniently than CLI parsing. For this app (polling every N minutes, a handful of repos), process-spawn overhead is irrelevant, and CLI git guarantees 100% behavioral compatibility with the user's actual git config (credential helpers, SSH config, `.gitconfig` aliases, hooks) — an independent reimplementation (libgit2) is a "most common 99%" implementation, and the 1% gap (auth quirks, config edge cases) is exactly the kind of thing that silently breaks a background tool users trust. Also: `git merge-tree --write-tree`'s specific conflict-info output format has no equivalent 1:1 pygit2 API — you'd still be shelling out for that one call, meaning you'd have two code paths (library + subprocess) instead of one. Use subprocess uniformly. |
| `py2app` | Briefcase (BeeWare) | Use if this were a cross-platform Toga UI app, or if you value automated signing/notarization over control. For *this* app specifically, avoid Briefcase: Toga's status-bar/menu-bar-only app support is still an in-development/prototype-stage feature as of the BeeWare project's own 2024-2025 status updates, not a shipped, stable pattern — you'd be building on immature ground for the one thing this app *is* (a menu bar app with no main window). `py2app` has no opinion about UI toolkit, bundles `rumps`/PyObjC apps directly (this is in fact the documented, common pairing — rumps' own examples reference py2app for packaging), and gives you full manual control over signing/notarization when you're ready, at the cost of doing those steps yourself (well-documented, one-time setup). |
| `gh` CLI (`gh pr checks --json`, `gh pr status`) | Raw GitHub REST/GraphQL API calls (`requests`/`httpx` + PAT) | Use raw API only if you need data `gh` doesn't expose or need to avoid a CLI subprocess dependency. Not applicable here — the PROJECT.md constraint explicitly requires reusing `gh auth login` with no new credentials, which raw API calls would violate (needs its own token). `gh pr checks --json name,state,bucket` and `gh pr status --json ...` give structured, jq-filterable JSON designed for exactly this scripting use case. |
## What NOT to Use
| Avoid | Why | Use Instead |
|-------|-----|--------------|
| `git merge-tree` legacy 3-argument mode (`git merge-tree <base> <branch1> <branch2>`) | Deprecated, conflict-marker-only output with no structured conflict metadata, cannot distinguish all conflict types (rename conflicts, mode conflicts, binary conflicts, modify/delete) | `git merge-tree --write-tree <branch1> <branch2>` (available since git 2.38, Oct 2022) — writes a merge result tree without touching the index or working copy, and reports conflicts in a structured "Conflicted file info" section |
| Parsing `git merge-tree --write-tree` output by diffing the returned tree object for conflict markers | The docs explicitly warn against this — numerous conflict types (modify/delete, mode conflicts, binary-both-sides, file/directory conflicts, rename permutations) aren't representable as in-file conflict markers | Parse the dedicated "Conflicted file info" section of the output, and check the **process exit status** (0 = clean, 1 = conflicts) — never infer cleanliness from an empty file list alone |
| `osascript display notification` as the permanent notification mechanism if you ever want actionable buttons | AppleScript notifications are fire-and-forget banners; they cannot register interactive notification categories/actions — that capability is exclusively `UNUserNotificationCenter`, which requires a code-signed app | Keep `osascript` for v1 (matches current no-signed-bundle constraint — this is correct as-is; `terminal-notifier` was evaluated as an alternative and rejected, unmaintained since 2017 and non-functional on macOS 26); migrate to `pyobjc-framework-UserNotifications` + `UNUserNotificationCenter` only when/if you build the signed `.app` for the deferred "one-click pull" feature |
| `shell=True` in any `subprocess` call touching repo paths or branch names | Command injection risk the moment a repo path or branch name contains a shell metacharacter (unlikely from you, but this becomes a real risk the moment "package for colleagues" happens and config files come from someone else) | Always pass argument lists: `subprocess.run(["git", "-C", repo_path, "fetch", ...], timeout=...)` |
| `imp`-module-era `py2app` versions with Python 3.12+ | `py2app` had a real breaking incompatibility with Python 3.12 (relied on the `imp` module, removed in 3.12) — confirmed via multiple GitHub issues (#491, #496) against `ronaldoussoren/py2app` | Use the current `py2app` 0.28.x series, which lists 3.12/3.13 support on PyPI — but **verify with a real build on your target Python version before committing**, since search results could not confirm the exact release that fixed this cleanly (flagged LOW confidence below, verify manually) |
## Stack Patterns by Variant
- Keep the prototype stack almost entirely as-is: `rumps`, `subprocess`+git, JSON config, `osascript`, `launchd` `KeepAlive`.
- Bump Python from 3.9 to 3.11 or 3.12 (3.9 EOL Oct 2025) — no code changes should be required, this is a pure interpreter/venv bump.
- Add `git merge-tree --write-tree` (not the legacy mode) for the new conflict-risk feature.
- Add `concurrent.futures.ThreadPoolExecutor` for the new parallel-fetch requirement.
- This is the correct, minimal-risk stack for what's actually in scope for this milestone.
- Do NOT rewrite in Swift. Wrap the existing Python/`rumps` codebase with `py2app` into a proper `.app` bundle, code-sign it (Developer ID Application certificate — Apple's docs and multiple sources confirm the notification permission dialog requires a signed app; ad-hoc signing sufficiency for `UNUserNotificationCenter` specifically could not be confirmed from available sources — treat as needing a real Developer ID cert, LOW-MEDIUM confidence on ad-hoc being enough), and add `pyobjc-framework-UserNotifications` for `UNUserNotificationCenter` + actionable categories.
- This also incidentally fixes the TCC/Full-Disk-Access fragility noted in PROJECT.md's "known gotchas": a stable, signed `.app` bundle gives TCC a consistent code identity to grant permissions to, instead of a bare interpreter path (`/usr/bin/python3`) whose permission grant can be invalidated by Python version/interpreter changes.
- Complexity delta is real but bounded: signing/notarization setup (Apple Developer account if not already enrolled, certificate, `codesign`, `notarytool` submission, stapling) is a one-time, well-documented process — not a rewrite. This is meaningfully cheaper than a Swift rewrite of the git/parsing logic.
## Version Compatibility
| Package A | Compatible With | Notes |
|-----------|------------------|-------|
| `rumps` (latest) | Python 3.9–3.13, macOS (any modern version — pure AppKit wrapper) | No known version ceiling found; low release cadence reflects a stable, "finished" API rather than incompatibility. |
| `py2app` 0.28.x | Python 3.12/3.13 listed as supported on current PyPI metadata | **Verify with your own build** — earlier 3.12 support was broken by `imp` module removal (GitHub issues #491/#496), and search results could not confirm the exact patch release where this was definitively resolved. Treat as MEDIUM confidence; do a smoke-test build before relying on it for the v2 milestone. |
| `git merge-tree --write-tree` | git ≥ 2.38 (Oct 2022) | Check `git --version` on first run; most 2024+ macOS installs (Xcode CLT or Homebrew) will satisfy this, but don't assume — add a version guard with a clear error message. |
| `gh pr checks --json` / `gh pr status --json` | Present in all actively maintained `gh` CLI releases (2.x line, currently in the 2.9x range) | JSON output flags are long-stable CLI surface, not a recent addition — low compatibility risk. |
## Sources
- https://github.com/jaredks/rumps — repo activity, examples referencing py2app pairing (MEDIUM confidence — WebFetch/API access blocked in this environment, relied on WebSearch snippets rather than direct repo inspection)
- https://pypi.org/project/rumps/ — version/release info (MEDIUM confidence, same access limitation)
- https://pypi.org/project/py2app/ , https://github.com/ronaldoussoren/py2app/issues/491 , /issues/496 — Python 3.12 `imp`-module breakage and 3.13 listing (MEDIUM confidence — issue existence confirmed, exact fix release not independently verified)
- https://briefcase.beeware.org/en/stable/reference/platforms/macOS/ , BeeWare "May 2024 Status Update" — Toga status-bar/menu-bar app support still prototype-stage (MEDIUM confidence)
- https://git-scm.com/docs/git-merge-tree — official docs confirming `--write-tree` mode, "Conflicted file info" parsing guidance, exit-status semantics (HIGH confidence, official docs)
- https://github.blog/open-source/git/highlights-from-git-2-38/ — confirms `--write-tree` mode shipped in git 2.38 (HIGH confidence, official source)
- https://cli.github.com/manual/gh_pr_checks , https://cli.github.com/manual/gh_pr_status — official `gh` CLI docs for `--json`/`--jq`/`bucket`/`--required`/`--watch` flags (HIGH confidence, official docs)
- https://www.pygit2.org/merge.html , https://github.com/libgit2/pygit2 — `Repository.merge_trees()` in-memory merge API, confirms pygit2 alternative exists but doesn't map 1:1 to `git merge-tree`'s structured conflict-info output (MEDIUM confidence)
- https://pypi.org/project/pyobjc-framework-UserNotifications/ , https://pyobjc.readthedocs.io/en/latest/apinotes/UserNotifications.html — confirms PyObjC bindings for `UNUserNotificationCenter` exist, and that code-signing is required for authorization (MEDIUM-HIGH confidence — official PyObjC docs)
- General WebSearch cross-referencing on TCC/launchd Full Disk Access fragility with bare interpreters vs app bundles (LOW-MEDIUM confidence, community sources — retained as directional pitfall context, not a hard technical claim)
<!-- GSD:stack-end -->

<!-- GSD:conventions-start source:CONVENTIONS.md -->
## Conventions

Conventions not yet established. Will populate as patterns emerge during development.
<!-- GSD:conventions-end -->

<!-- GSD:architecture-start source:ARCHITECTURE.md -->
## Architecture

Architecture not yet mapped. Follow existing patterns found in the codebase.
<!-- GSD:architecture-end -->

<!-- GSD:skills-start source:skills/ -->
## Project Skills

No project skills found. Add skills to any of: `.claude/skills/`, `.agents/skills/`, `.cursor/skills/`, `.github/skills/`, or `.codex/skills/` with a `SKILL.md` index file.
<!-- GSD:skills-end -->

<!-- GSD:workflow-start source:GSD defaults -->
## GSD Workflow Enforcement

Before using Edit, Write, or other file-changing tools, start work through a GSD command so planning artifacts and execution context stay in sync.

Use these entry points:
- `/gsd-quick` for small fixes, doc updates, and ad-hoc tasks
- `/gsd-debug` for investigation and bug fixing
- `/gsd-execute-phase` for planned phase work

Do not make direct repo edits outside a GSD workflow unless the user explicitly asks to bypass it.
<!-- GSD:workflow-end -->



<!-- GSD:profile-start -->
## Developer Profile

> Profile not yet configured. Run `/gsd-profile-user` to generate your developer profile.
> This section is managed by `generate-claude-profile` -- do not edit manually.
<!-- GSD:profile-end -->

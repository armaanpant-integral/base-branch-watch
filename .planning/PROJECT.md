# base-branch-watch

## What This Is

A macOS menu-bar companion app that eliminates "I forgot to `git pull`" and catches merge conflicts before they happen. It watches configured repos' base branches in the background, surfaces drift in the menu bar, and gates every `git push` with a conflict-risk check and a summary of what changed on the base branch since you last synced. Built for a single lazy dev first (Armaan), architected so it can later be handed to colleagues without a rewrite.

## Core Value

Never manually fetch or check a base branch again, and never get surprised by a merge conflict in a PR — code in peace, get told what changed and whether it conflicts right before you push.

## Requirements

### Validated

(None yet — ship to validate)

### Active

- [ ] User can watch multiple repos, each with one or more base branches (not just one base per repo)
- [ ] Menu bar shows at-a-glance status per repo: up to date / behind / diverged / conflict risk
- [ ] Background polling detects when a repo is behind its base branch (existing prototype behavior, carried forward)
- [ ] Background polling detects when local branch is ahead of its own origin (forgot to push)
- [ ] Status distinguishes "behind" (fast-forward pull) from "diverged" (behind AND ahead — needs real merge/rebase)
- [ ] Continuous lightweight conflict-risk heuristic: diff incoming base-branch commits' changed files against the user's locally modified/branch-unique files; overlap escalates the repo's badge to "⚠️ conflict risk" instead of a plain "behind" count
- [ ] Pre-push git hook, auto-installed into every watched repo: on every `git push`, always shows a summary of what's new on the base branch since the user's branch diverged
- [ ] Pre-push hook escalates to an interactive y/n prompt ("Push anyway?") when the authoritative check (`git merge-tree` dry-run merge, no working-tree changes) finds real file-level conflicts — default is to abort, user can proceed anyway; hook is a warn-and-gate, never a hard, unbypassable block
- [ ] Digest notifications: one consolidated banner per polling cycle listing all repos needing attention, not one popup per repo
- [ ] Repo fetches run in parallel/batched per cycle, not sequential one-by-one, so wall-clock time and process-spawn overhead don't scale linearly with repo count
- [ ] PR status for the current branch (open PR checks/review/mergeable state) shown in the menu, sourced via the `gh` CLI (reuses existing `gh auth`, no new credentials)
- [ ] Add/remove watched repos from the menu bar UI (native folder picker + base-branch auto-detect via `git ls-remote --symref origin HEAD`, override allowed) — carried forward from prototype
- [ ] Auto-starts at login, survives crashes (launchd LaunchAgent, `KeepAlive`) — carried forward from prototype
- [ ] Codebase is modular (git operations, config/state persistence, notification delivery, menu UI, git-hook installer, PR-status fetching are separable) so v2 packaging for colleagues doesn't require a rewrite

### Out of Scope

- **Cloud-hosted / server component** — stays local-first and git-native; no external service to run or pay for. Rejected earlier for the same reason a claude.ai cloud routine and GitHub Actions + Slack webhook were rejected (1hr min interval, touches shared repo state, or needs a bridge server).
- **Browser extension** — extensions can't run `git`/shell directly; would still need a local server bridge underneath, making it a thin UI layer with less native integration (no folder picker, no native notifications) than the menu bar app already has.
- **Windows/Linux support** — current stack (`rumps`/PyObjC, `launchd`, `osascript`) is macOS-only by construction. Cross-platform would mean a different UI/notification/scheduling layer entirely; not attempted until macOS version is solid.
- **Cross-repo/team visibility (seeing teammates' branch status)** — this is a personal awareness tool, not a team dashboard. Sharing means "colleagues can install and run their own instance," not "see each other's status."
- **Company-wide packaging / install script / distribution** — deferred to v2. v1 priority is personal reliability; the architecture must not block packaging later, but building the installer now is not in scope.
- **CI-awareness on base branch** (skip/downgrade pull nudges when base's CI is red) — deferred to v2. Real value, but adds a second polling dimension and cross-provider complexity disproportionate to the core value prop.
- **Uncommitted-changes aging / stale local branch detection** — deferred to v2. A different laziness problem (forgetting to commit / clean up) than base-branch drift; would dilute v1 focus.
- **One-click "Pull now" notification action button** — deferred to v2 (flagged as a real technical risk: macOS actionable notification buttons need `UNUserNotificationCenter` with a signed `.app` bundle registering notification categories, a meaningfully bigger lift than everything else here, likely needs its own spike before committing to it).

## Context

- **Origin**: grew out of a real, immediate need — forgetting to `git pull`/merge `perps-funding-rate-dev` into a feature branch on the `crypto-trading` repo. Started as a single hardcoded bash script polled via `launchd` `StartInterval` with `osascript` notifications, iterated into a Python `rumps`/PyObjC menu bar app (`~/.claude/scripts/base_branch_watch_app.py`) supporting multiple repos via a JSON config, with add/remove repos from the menu, per-repo logging, and daily log rotation. That prototype is the reference implementation this project formalizes and extends — not a green-field design from scratch.
- **Known gotchas from the prototype** (carry forward as constraints/pitfalls, don't re-discover):
  - macOS TCC (Transparency, Consent, Control) blocks protected-folder access (Desktop, Documents, Downloads) per-executable, not per-user. Any interpreter launchd spawns directly (`/bin/bash`, `/usr/bin/python3`) needs its own Full Disk Access grant — hit twice already (once per interpreter switch).
  - TCC grants only take effect on process restart, not dynamically on an already-running process — granting Full Disk Access requires `launchctl bootout` + `bootstrap`, not just a refresh.
  - `osascript display notification` needs Banners explicitly enabled (System Settings → Notifications) for the invoking process — sound-only is the default failure mode if banners are off.
  - A long-running app (`KeepAlive: true` in the LaunchAgent) means "Quit" from the menu or `kill` doesn't durably stop it — launchd respawns it. Only `launchctl bootout` actually disables it.
- **Distribution auth note**: the prototype's target repo (`crypto-trading`, at `IntegralCorp/crypto-trading`) is not connected to the Claude GitHub App, which is why a claude.ai cloud routine wasn't viable — local `gh auth login` and SSH-based `git` access are separate from that, and are what this tool actually relies on.

## Constraints

- **Platform**: macOS only — `rumps`/PyObjC for the menu bar UI, `launchd` for scheduling/autostart, `osascript` for notifications. No cross-platform requirement for v1.
- **No new external services or credentials**: must work with what's already authenticated locally (SSH keys via `ssh-agent`, `gh auth login`) — no new API keys, no hosted backend, no message-queue/webhook infrastructure.
- **No signed app bundle for v1**: sticking with a plain Python script run via `python3`, not a compiled/signed `.app`. This is *why* actionable notification buttons are out of scope for v1 — they need the bundle infrastructure this constraint excludes.
- **Personal-first architecture**: single user, single machine for v1 — but code should avoid hardcoding assumptions (e.g. absolute personal paths baked into logic rather than config) that would make v2 sharing a rewrite rather than a packaging exercise.

## Key Decisions

| Decision | Rationale | Outcome |
|----------|-----------|---------|
| Native macOS menu bar app (rumps/PyObjC), not a cloud routine, GitHub Actions webhook, or browser extension | Cloud routine needed GitHub App connection + had a 1hr min interval; Actions needed a workflow file on the shared base branch + Slack webhook + repo secret; browser extension needs a local server bridge anyway with less native integration | ✓ Good — prototype built and working |
| `launchd` `KeepAlive`, not `StartInterval` | App is a long-running process with its own internal polling timer, not a short script re-invoked every interval | ✓ Good |
| Base branch auto-detected via `git ls-remote --symref origin HEAD` when adding a repo, override allowed | Avoids wrong guesses, but some repos intentionally watch a non-default integration branch (e.g. `perps-funding-rate-dev`, not `main`) | ✓ Good |
| Pre-push conflict check is an interactive y/n gate, not a silent warning and not a hard unbypassable block | Silent warn-and-continue makes the check toothless ("what's the point"); a hard block removes user agency and fights normal git escape hatches | — Pending, not yet built |
| Company-wide packaging deferred to v2 | v1 priority is personal reliability; premature packaging work would delay the actual value | — Pending |
| v1 optional-feature split: PR status via `gh` CLI in, CI-awareness / staleness warnings / one-click pull button deferred | Best effort-to-value ratio — PR status reuses existing auth and directly serves the "what's the state of things before I push" moment; the other three add scope or real technical risk disproportionate to core value | — Pending |

---
*Last updated: 2026-07-09 after initialization*

## Evolution

This document evolves at phase transitions and milestone boundaries.

**After each phase transition** (via `/gsd-transition`):
1. Requirements invalidated? → Move to Out of Scope with reason
2. Requirements validated? → Move to Validated with phase reference
3. New requirements emerged? → Add to Active
4. Decisions to log? → Add to Key Decisions
5. "What This Is" still accurate? → Update if drifted

**After each milestone** (via `/gsd:complete-milestone`):
1. Full review of all sections
2. Core Value check — still the right priority?
3. Audit Out of Scope — reasons still valid?
4. Update Context with current state

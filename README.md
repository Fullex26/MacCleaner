# 🧹 MacCleaner

> macOS developer storage cleanup — CLI, menu bar app, and an agent-ready JSON interface.

[![Build](https://img.shields.io/github/actions/workflow/status/Fullex26/MacCleaner/ci.yml?branch=main&style=for-the-badge&label=BUILD)](https://github.com/Fullex26/MacCleaner/actions/workflows/ci.yml)
[![Release](https://img.shields.io/github/v/release/Fullex26/MacCleaner?style=for-the-badge&label=RELEASE)](https://github.com/Fullex26/MacCleaner/releases/latest)
[![Stars](https://img.shields.io/github/stars/Fullex26/MacCleaner?style=for-the-badge&label=STARS)](https://github.com/Fullex26/MacCleaner/stargazers)
[![License](https://img.shields.io/github/license/Fullex26/MacCleaner?style=for-the-badge&label=LICENSE)](LICENSE)

MacCleaner finds and removes the developer detritus that accumulates silently — Xcode DerivedData, Docker layers, package manager caches, downloaded AI models, stale `node_modules` in projects you abandoned months ago. A single command can reclaim tens of gigabytes.

- **CLI** — `scan`, `clean`, `projects`, `report`, `doctor`, `config`. 83 cleanup targets across 23 categories. Interactive checklist or fully unattended.
- **macOS app, redesigned in v2.6** — sidebar navigation, glass panels, and a rich menu bar popover (disk ring, reclaimable hero number, top categories, one-click "Clean safe items") over the same engine and config as the CLI.
- **Agent-ready** — every command speaks `--json`, every target has a stable ID, exit codes are documented. Point your AI agent at [AGENTS.md](AGENTS.md) and it can operate the whole tool.
- **Safe by design** — deletes only inside your home directory, never follows symlinks, and can move things to the Trash instead of deleting.
- **Know before you act** — `--dry-run` previews the exact paths and sizes a clean would touch with zero side effects, `report` tracks disk-space trends over time, and `projects` automatically skips repos with uncommitted or unpushed work.
- **AI-era cleanup** — finds stale build/clone litter that AI coding sessions leave under `/private/tmp` (classified by contents, never by name, and always review-only), plus unused iOS simulator devices and runtime images via `simctl`.
- **App Uninstaller** — the `leftovers` category finds cache, preference, and saved-state files an app left behind under `~/Library` after you removed it, matched by bundle ID (never by name or fuzzy guessing) against every currently-installed app. Always review-only, never auto-cleaned.
- **Stay in the loop** — a notification when a scheduled clean finishes, a low-disk warning if free space drops below a configurable threshold (10 GB by default), and a menu bar that shows free space and "last cleaned" without opening the app.
- **Updates itself** — the app checks for new versions daily via Sparkle and prompts with release notes when one's available (or check manually from Settings); installed via Homebrew? `brew upgrade` still works as before. Sparkle only updates the app bundle, not the installed CLI engine at `~/mac-cleaner/cleaner.py` — re-run `bash install.sh` to update that too (`maccleaner doctor` flags a version mismatch between the two).

---

## Install

### Homebrew (app only — signed & notarized)

```bash
brew install --cask Fullex26/tap/maccleaner
```

Use this exact fully-qualified form — it taps and trusts `Fullex26/tap` in one step (the separate `brew tap` + `brew install` two-step fails on tap trust under Homebrew 6). Installs the menu bar app to `/Applications`; the app bundles its own engine, so no separate CLI install is needed. For the CLI shortcuts and shell completions too, use the full install below.

### Full install (CLI + app + completions + scheduling)

```bash
git clone https://github.com/Fullex26/MacCleaner && cd MacCleaner && bash install.sh
```

Installs to `~/mac-cleaner/`, adds shell aliases (`maccleaner`, `mclean`, `mpreview`, `mreport`), sets up zsh/bash tab completions (subcommands, flags, config keys, and live category/target IDs — restart your shell to use them), optionally sets up a launchd schedule, and installs the menu bar app. Already scheduling via cron? It's migrated to launchd automatically the next time you run `scheduler.sh weekly` or `scheduler.sh monthly`.

**Zero required dependencies** — the engine is a single Python 3 file using only the standard library, and Python 3 ships on every Mac. [`rich`](https://github.com/Textualize/rich) is optional (prettier tables); `install.sh` offers to install it, and everything works without it.

Upgrading from v1? Everything still works — old flags (`--preview`, `--clean --yes`), existing cron jobs, aliases, and the old menu bar app are all still supported.

---

## Quick Start

```bash
maccleaner scan             # what's reclaimable, and how much
maccleaner clean            # interactive checklist — pick what goes
maccleaner clean --yes      # auto-clean everything marked safe (unattended mode)
maccleaner projects         # find stale build artifacts in old projects
maccleaner storage-insights # read-only: largest files sitting in Documents/Downloads/Desktop
maccleaner doctor           # health-check your environment and install
```

A scan looks like this:

```
  [xcode    ] Xcode DerivedData                        14.2 GB  safe
  [ai       ] Hugging Face hub cache                    9.8 GB  REVIEW
  [node     ] pnpm store                                3.1 GB  safe
  [python   ] uv cache                                  1.7 GB  safe
  [caches   ] Playwright browsers                       1.2 GB  REVIEW
  ...

  Total reclaimable: 31.4 GB

  → Run 'maccleaner clean' to start cleaning
```

`projects` is the one people miss: it walks your project roots (`~/Documents`, `~/Developer`, `~/Projects`, `~/Code`, `~/dev` by default) looking for `node_modules`, `.venv`, `target`, `Pods`, `.next`, and friends that haven't been touched in 30+ days — and only counts a directory when a sibling manifest (`package.json`, `Cargo.toml`, `pyproject.toml`, …) proves what it is. Add `--clean` to remove them. Projects with uncommitted changes or commits that haven't been pushed anywhere are flagged and left out of `--clean --yes` automatically — name them explicitly with `--targets` if you really want them gone.

Useful extras: `--category xcode` to scope a run, `--min-size 500` to ignore small stuff, `--trash` to move to Trash instead of deleting, `--dry-run` to see exactly what would be deleted first, `report` for history and disk-space trends. The full flag reference lives in [AGENTS.md](AGENTS.md).

---

## Menu Bar App

A SwiftUI app (macOS 13+) that's a thin client over the CLI — same engine, same config, no separate logic. Redesigned in v2.6 ("Glass & Sparkle"): sidebar navigation, glass panels, monospaced sizes, per-category color dots, and one design system driving both light and dark mode.

- **Menu bar popover**: a disk-usage ring, your reclaimable space as a hero number, your top 3 categories, and a one-click "Clean safe items" button with a self-expiring "freed" confirmation — no need to open the full window for a quick clean. No Dock icon. Refreshes lightly every minute, with a full rescan on a longer interval, on wake, and whenever you open the popover.
- **Dashboard window**: sidebar-navigated — **Dashboard** (a free-space trend chart and a "Large Files" panel surfacing the biggest files in Documents/Downloads/Desktop with one-click Reveal in Finder, both above the targets grouped by category with checkboxes, All/None/Safe-only selection, and clean in-app with live per-item progress), **Projects** (stale artifact finder with Select All/None), **History** (past runs), **Settings** (category toggles and delete mode, shared with the CLI's `config.json`; the cleanup schedule, which lives in launchd plists managed via the CLI's `schedule` subcommand, not in `config.json`; and "Check for Updates…", backed by Sparkle).

`install.sh` builds the app from source whenever Swift's toolchain (`swiftc`, from Xcode or the Command Line Tools) is available, so the installed app is never older than your checkout; it falls back to the committed pre-built copy only when `swiftc` isn't found. No Xcode project needed either way:

```bash
bash app/build.sh            # swiftc → build/MacCleaner.app (universal, ad-hoc signed)
bash app/build.sh --install  # …then copy to ~/Applications
```

The bundle includes `cleaner.py` as a fallback engine, so the app works even without `install.sh`. Releases from `git tag`/GitHub Actions are signed and notarized when the maintainer's signing secrets are configured (see `docs/RELEASING.md`); a local `app/build.sh` run always ad-hoc signs, so if you build it yourself, the first launch may still need Right-click → Open → Open once.

---

## For AI Agents

MacCleaner is built to be operated by agents, not just humans: every data command takes `--json` (data on stdout, messages on stderr), every cleanup target has a stable kebab-case ID, and exit codes are documented (0 success, 1 runtime error, 2 usage error). An agent can scan, decide, and clean precisely — no TTY, no prompts, no parsing tables.

```bash
python3 ~/mac-cleaner/cleaner.py clean --targets npm-cache,pip-cache,xcode-derived-data --yes --json
```

**[AGENTS.md](AGENTS.md)** is the machine contract: all commands and flags, JSON schemas, the full target ID list, and safety semantics.

---

## What It Cleans

23 categories, 83 targets. Enable or disable any category via `maccleaner config enable|disable <category>`; run `maccleaner categories` to list every target and its ID.

| Category | What's in it |
|----------|--------------|
| **xcode** | DerivedData, Previews, device support, simulator caches, SwiftPM/Carthage |
| **docker** | Unused images, containers, and build cache |
| **node** | npm / npx / pnpm / yarn / bun / deno / node-gyp caches |
| **python** | pip / uv / poetry / ruff caches, pyenv shims |
| **caches** | App caches — Claude, Cursor, Chrome, Slack, Discord, Spotify, Electron, Playwright, … |
| **logs** | Oversized log folders in `~/Library/Logs` (over 100 MB by default) |
| **homebrew** | Download cache and unused dependencies |
| **go** | Module, package, and build caches |
| **rust** | Cargo registry and git caches |
| **ruby** | Stale gem versions |
| **cocoapods** | CocoaPods cache |
| **gradle** | Gradle build caches |
| **maven** | Maven local repository |
| **ai** | Downloaded models — Hugging Face hub, PyTorch hub, Ollama, LM Studio, Whisper (re-downloadable) — plus Codex CLI session transcripts (conversation history, *not* re-downloadable) |
| **ide** | VS Code and JetBrains caches |
| **browsers** | Arc, Brave, Edge, Firefox caches |
| **system** | Empty Trash, iOS device backups — review carefully |
| **flutter** | Dart & Flutter pub package cache |
| **php** | Composer package cache |
| **vms** | VM disks and container runtimes — Colima, Vagrant, minikube (review carefully) |
| **tmp** | Stale build/clone artifacts left in `/private/tmp` by tools and AI coding sessions, classified by contents not name — review carefully |
| **simulators** | Stale iOS simulator devices and unused runtime images, via `simctl` — review carefully |
| **leftovers** | Cache, preference, and saved-state files left behind by apps you've already deleted, matched by bundle ID — review carefully |

---

## Safety

- **Safe vs. Review** — every target is flagged. *Safe* targets (caches, build products) are cleaned by `--yes` and the app's Auto-Clean. *Review* targets (archives, AI models, Trash, iOS backups) always require either interactive confirmation or explicit selection (`--targets <id> --yes` counts as explicit consent).
- **Home-only** — the engine refuses to delete anything outside `$HOME`, and refuses `$HOME` itself. Symlinks are never followed; they're unlinked in place.
- **Empty-only semantics** — `~/Library/Caches` and `~/.Trash` have their *contents* cleared; the directories themselves are never removed.
- **Trash mode** — pass `--trash` (or set `"delete_mode": "trash"` in config) to move items to `~/.Trash` instead of deleting, so any run is recoverable until you empty the Trash.

---

## Configuration

`~/mac-cleaner/config.json` — shared by the CLI and the app:

```json
{
  "enabled_categories": ["xcode", "docker", "node", "..."],
  "skip_paths": [],
  "log_threshold_mb": 100,
  "auto_approve": false,
  "delete_mode": "rm",
  "project_roots": ["~/Documents", "~/Developer", "~/Projects", "~/Code", "~/dev"],
  "project_min_age_days": 30,
  "tmp_min_age_days": 3,
  "simulator_stale_days": 30,
  "notifications": true,
  "low_disk_alerts": true,
  "low_disk_threshold_gb": 10
}
```

Manage it from the CLI instead of editing by hand:

```bash
maccleaner config show
maccleaner config enable ai
maccleaner config disable docker
maccleaner config set delete_mode trash
```

---

## Scheduling

```bash
~/mac-cleaner/scheduler.sh weekly    # Every Monday 9am
~/mac-cleaner/scheduler.sh monthly   # 1st of month
~/mac-cleaner/scheduler.sh status    # Check current schedule
~/mac-cleaner/scheduler.sh remove    # Remove schedule
```

Scheduled runs use `clean --yes --notify`, so only *safe* targets are ever touched unattended, and you get a notification once it's done. An hourly low-disk check runs alongside it, warning (at most once a day) if free space drops below `low_disk_threshold_gb`.

Scheduling uses launchd, not cron — it catches up on a run that was due while your Mac was asleep instead of silently skipping it. If you already had a cron schedule from an earlier version, it's migrated to launchd automatically the next time you run `scheduler.sh weekly`/`monthly`.

`scheduler.sh` is a thin wrapper around `cleaner.py schedule status|weekly|monthly|off` — the menu bar app's Settings tab drives the same command, so you can turn scheduling on, off, or switch cadence from the app instead of the terminal.

---

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) and [ROADMAP.md](ROADMAP.md). The easiest contribution is a new cleanup target — one small `add(...)` entry in `get_targets()` in `cleaner.py`. Run the tests with:

```bash
python3 -m unittest discover -s tests
```

---

## License

[MIT](LICENSE) — Jordan Fuller, 2026.

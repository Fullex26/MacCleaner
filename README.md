# 🧹 MacCleaner

> macOS developer storage cleanup — CLI, menu bar app, and an agent-ready JSON interface.

[![Build](https://img.shields.io/github/actions/workflow/status/Fullex26/MacCleaner/ci.yml?branch=main&style=for-the-badge&label=BUILD)](https://github.com/Fullex26/MacCleaner/actions/workflows/ci.yml)
[![Release](https://img.shields.io/github/v/release/Fullex26/MacCleaner?style=for-the-badge&label=RELEASE)](https://github.com/Fullex26/MacCleaner/releases/latest)
[![Stars](https://img.shields.io/github/stars/Fullex26/MacCleaner?style=for-the-badge&label=STARS)](https://github.com/Fullex26/MacCleaner/stargazers)
[![License](https://img.shields.io/github/license/Fullex26/MacCleaner?style=for-the-badge&label=LICENSE)](LICENSE)

MacCleaner finds and removes the developer detritus that accumulates silently — Xcode DerivedData, Docker layers, package manager caches, downloaded AI models, stale `node_modules` in projects you abandoned months ago. A single command can reclaim tens of gigabytes.

- **CLI** — `scan`, `clean`, `projects`, `report`, `doctor`, `config`. 70+ cleanup targets across 20 categories. Interactive checklist or fully unattended.
- **macOS app** — SwiftUI menu bar app (macOS 13+) with a dashboard: see everything, tick what goes, clean in-app.
- **Agent-ready** — every command speaks `--json`, every target has a stable ID, exit codes are documented. Point your AI agent at [AGENTS.md](AGENTS.md) and it can operate the whole tool.
- **Safe by design** — deletes only inside your home directory, never follows symlinks, and can move things to the Trash instead of deleting.
- **Know before you act** — `--dry-run` previews the exact paths and sizes a clean would touch with zero side effects, `report` tracks disk-space trends over time, and `projects` automatically skips repos with uncommitted or unpushed work.

---

## Install

```bash
git clone https://github.com/Fullex26/MacCleaner && cd MacCleaner && bash install.sh
```

Installs to `~/mac-cleaner/`, adds shell aliases (`maccleaner`, `mclean`, `mpreview`, `mreport`), optionally sets up a cron schedule, and installs the menu bar app.

**Zero required dependencies** — the engine is a single Python 3 file using only the standard library, and Python 3 ships on every Mac. [`rich`](https://github.com/Textualize/rich) is optional (prettier tables); `install.sh` offers to install it, and everything works without it.

Upgrading from v1? Everything still works — old flags (`--preview`, `--clean --yes`), existing cron jobs, aliases, and the old menu bar app are all still supported.

---

## Quick Start

```bash
maccleaner scan             # what's reclaimable, and how much
maccleaner clean            # interactive checklist — pick what goes
maccleaner clean --yes      # auto-clean everything marked safe (cron mode)
maccleaner projects         # find stale build artifacts in old projects
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

A SwiftUI app (macOS 13+) that's a thin client over the CLI — same engine, same config, no separate logic.

- **Menu bar**: total reclaimable space at a glance, plus Scan, Auto-Clean Safe, Open Dashboard, Quit. No Dock icon.
- **Dashboard window**: four tabs — **Dashboard** (targets grouped by category with checkboxes, clean in-app), **Projects** (stale artifact finder), **History** (past runs), **Settings** (category toggles and delete mode, shared with the CLI's `config.json`).

`install.sh` installs a pre-built copy, or build from source — no Xcode project needed:

```bash
bash app/build.sh            # swiftc → build/MacCleaner.app (universal, ad-hoc signed)
bash app/build.sh --install  # …then copy to ~/Applications
```

The bundle includes `cleaner.py` as a fallback engine, so the app works even without `install.sh`.

> **First launch:** macOS may warn about unsigned apps. Right-click → Open → Open. Notarization is on the [roadmap](ROADMAP.md).

---

## For AI Agents

MacCleaner is built to be operated by agents, not just humans: every data command takes `--json` (data on stdout, messages on stderr), every cleanup target has a stable kebab-case ID, and exit codes are documented (0 success, 1 runtime error, 2 usage error). An agent can scan, decide, and clean precisely — no TTY, no prompts, no parsing tables.

```bash
python3 ~/mac-cleaner/cleaner.py clean --targets npm-cache,pip-cache,xcode-derived-data --yes --json
```

**[AGENTS.md](AGENTS.md)** is the machine contract: all commands and flags, JSON schemas, the full target ID list, and safety semantics.

---

## What It Cleans

20 categories, 70+ targets. Enable or disable any category via `maccleaner config enable|disable <category>`; run `maccleaner categories` to list every target and its ID.

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
| **ai** | Downloaded models — Hugging Face hub, PyTorch hub, Ollama (re-downloadable) |
| **ide** | VS Code and JetBrains caches |
| **browsers** | Arc, Brave, Edge, Firefox caches |
| **system** | Empty Trash, iOS device backups — review carefully |
| **flutter** | Dart & Flutter pub package cache |
| **php** | Composer package cache |
| **vms** | VM disks and container runtimes — Colima, Vagrant, minikube (review carefully) |

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
  "project_min_age_days": 30
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

Scheduled runs use `clean --yes`, so only *safe* targets are ever touched unattended.

---

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) and [ROADMAP.md](ROADMAP.md). The easiest contribution is a new cleanup target — one small `add(...)` entry in `get_targets()` in `cleaner.py`. Run the tests with:

```bash
python3 -m unittest discover -s tests
```

---

## License

[MIT](LICENSE) — Jordan Fuller, 2026.

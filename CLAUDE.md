# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

MacCleaner (v2) is a macOS developer storage cleanup tool with two components:
1. **`cleaner.py`** — Python 3 CLI, single file, stdlib-only (the core engine for all scan/delete logic)
2. **`app/`** — SwiftUI menu bar + dashboard app (macOS 13+), a thin client over the CLI's JSON interface

## Commands

### CLI (subcommands are the primary interface)
```bash
python3 cleaner.py                    # No args = welcome screen
python3 cleaner.py scan               # What can be cleaned + sizes (--category, --min-size MB, --all, --json)
python3 cleaner.py clean              # Interactive cleanup (curses TUI checklist)
python3 cleaner.py clean --yes        # Auto-approve all safe targets (cron mode)
python3 cleaner.py clean --targets npm-cache,pip-cache --yes --json   # Agent mode: specific IDs
python3 cleaner.py clean --trash      # Move to ~/.Trash instead of deleting
python3 cleaner.py projects           # Stale build artifacts (--roots, --min-age-days, --clean, --yes, --targets, --trash, --json)
python3 cleaner.py report -n 10       # Cleanup history from report.log (--json)
python3 cleaner.py doctor             # Environment health check (--json)
python3 cleaner.py categories         # List categories + targets (--json)
python3 cleaner.py config show|path|enable C|disable C|set KEY VALUE
python3 cleaner.py install-deps       # Install 'rich' for pretty output
```

Every data command supports `--json`. JSON goes to **stdout**, human messages to **stderr**. Exit codes: 0 success, 1 runtime error, 2 usage error. `AGENTS.md` documents the machine contract.

**Legacy v1 spellings still work** via `translate_legacy()` pre-parse translation: `--preview`, `--clean [--yes]`, `--report`, bare `--json` (maps to `scan --json` — the old menu bar app contract), `--category`, `--config-show/--config-enable/--config-disable`, `--install-deps`; also aliases `preview`→`scan`, `history`→`report`. Existing cron jobs, shell aliases, and the v1 app keep working — don't break these.

Categories (17): `xcode`, `docker`, `node`, `python`, `caches`, `logs`, `homebrew`, `go`, `rust`, `ruby`, `cocoapods`, `gradle`, `maven`, `ai`, `ide`, `browsers`, `system`

### Tests
```bash
python3 -m unittest discover -s tests    # 39 tests, stdlib only, no deps
```
CI runs tests + smoke tests + the app build on `macos-latest`.

### Install & Schedule
```bash
bash install.sh                        # Copies to ~/mac-cleaner/, adds aliases, installs the app
~/mac-cleaner/scheduler.sh weekly|monthly|remove|status
```

After install, zsh aliases: `maccleaner`, `mclean`, `mpreview`, `mreport`.

### App Build
```bash
bash app/build.sh              # swiftc → build/MacCleaner.app (universal arm64+x86_64 when possible, ad-hoc signed)
bash app/build.sh --install    # …then copy to ~/Applications/
```
No Xcode project needed. The build bundles `cleaner.py` into `Contents/Resources/` as a fallback engine.

## Architecture

### Python ↔ Swift Bridge (the JSON contract)
The Swift app has **no cleaning logic**. `CleanerBridge.swift` runs `cleaner.py` via `Process` and decodes JSON from stdout into `Codable` models (`ScanReport`, `CleanResult`, `ProjectsReport`, `HistoryReport`, `CategoriesReport`).

**Superset rule**: additive JSON changes (new keys) are fine — already-installed apps keep decoding. Removing or renaming keys breaks the app models in `app/Sources/CleanerBridge.swift` AND the documented contract in `AGENTS.md` — update both if you must.

Engine resolution order: `MACCLEANER_ENGINE` env override (dev) → `~/mac-cleaner/cleaner.py` → bundled `Contents/Resources/cleaner.py`.

### App structure (`app/Sources/`)
- `MacCleanerApp.swift` — `MenuBarExtra` (reclaimable size, Scan, Auto-Clean Safe, Open, Quit; `LSUIElement`, no Dock icon) + window with 4 tabs
- `DashboardView.swift` — grouped targets with checkboxes, in-app clean via `clean --targets … --yes --json`
- `ProjectsView.swift`, `HistoryView.swift`, `SettingsView.swift` — Settings persists through `config` subcommands so CLI and app share one config

### Python cleaner.py internals
- `get_targets(config)` — 60+ targets across 17 categories. Each has a **stable kebab-case `id`** (e.g. `xcode-derived-data`, `npm-cache`), `label`, `description`, `safe` bool, and either a `path` (glob patterns with `*` supported), a `cmd` (docker prune, brew cleanup, …), or `empty_only=True` (delete contents, keep dir — used for `~/Library/Caches` and `~/.Trash`)
- `measure_targets(targets)` — parallel `du -sk` via `ThreadPoolExecutor`; cmd targets use `estimate_cmd` parsers
- `delete_target(t, mode)` — refuses anything outside `$HOME` (and `$HOME` itself / `/`), never follows symlinks (unlinks them); `mode="trash"` moves to `~/.Trash` instead (the `trash` target always hard-deletes)
- `scan_projects(config)` — walks `project_roots` to bounded depth for artifact dirs (`node_modules`, `.venv`, `target`, `build`, `Pods`, `.next`, …) requiring a sibling manifest (`package.json`, `Cargo.toml`, `pyproject.toml`, …) and min age (default 30 days); never descends into artifacts
- `run_doctor(config)` — checks python, rich, config validity, install, cron, app, tool availability, disk
- `run_tui_clean(targets)` — curses checklist; falls back to y/N prompts when non-interactive
- `translate_legacy(argv)` — v1 flag → v2 subcommand shim, runs before argparse
- `report.log` (sibling to `cleaner.py`) stores the last 50 runs as JSON

### Safe vs. Review distinction
Each target has a `safe` bool. `--yes` / `auto_approve` only cleans `safe=True` targets. Review targets (`safe=False` — Xcode Archives, AI models, iOS backups, Trash, …) need explicit selection (`--targets id --yes` counts as consent) or interactive confirmation.

### Config
`config.json` (sibling to `cleaner.py`; installed: `~/mac-cleaner/config.json`) — missing keys merge with `DEFAULT_CONFIG` at load. Keys: `enabled_categories`, `skip_paths`, `log_threshold_mb`, `auto_approve`, `schedule`, `delete_mode` (`"rm"` | `"trash"`), `project_roots`, `project_min_age_days`.

### Env vars
- `MACCLEANER_CONFIG` / `MACCLEANER_LOG` — override config/log paths (used by tests)
- `MACCLEANER_ENGINE` — app-side override of the engine path (app development)

## Install Path vs. Source
Source lives in this repo; installed copy lives at `~/mac-cleaner/`, app at `~/Applications/MacCleaner.app`. When testing changes, either re-run `install.sh` or call `cleaner.py` directly from the repo path (for the app, set `MACCLEANER_ENGINE` to the repo's `cleaner.py`).

## Optional Dependency
`rich` is optional — detected at import, falls back to plain text (`RICH = False`). All output paths have both rich and plain variants.

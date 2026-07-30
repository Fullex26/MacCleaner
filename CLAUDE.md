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
python3 cleaner.py clean --yes        # Auto-approve all safe targets (unattended/scheduled mode)
python3 cleaner.py clean --targets npm-cache,pip-cache --yes --json   # Agent mode: specific IDs
python3 cleaner.py clean --trash      # Move to ~/.Trash instead of deleting
python3 cleaner.py clean --dry-run --json   # Preview exact paths/sizes a clean would touch, zero side effects
python3 cleaner.py clean --yes --notify     # Post a macOS notification summarizing what was freed (used by the launchd agent)
python3 cleaner.py projects           # Stale build artifacts (--roots, --min-age-days, --clean, --yes, --targets, --trash, --dry-run, --json)
python3 cleaner.py report -n 10       # Cleanup history from report.log (--json)
python3 cleaner.py doctor             # Environment health check (--json)
python3 cleaner.py categories         # List categories + targets (--json)
python3 cleaner.py config show|path|enable C|disable C|set KEY VALUE
python3 cleaner.py disk-check         # Cheap low-disk warning check, for launchd (--json); always exits 0
python3 cleaner.py install-deps       # Install 'rich' for pretty output
```

Every data command supports `--json`. JSON goes to **stdout**, human messages to **stderr**. Exit codes: 0 success, 1 runtime error, 2 usage error. `AGENTS.md` documents the machine contract.

**Legacy v1 spellings still work** via `translate_legacy()` pre-parse translation: `--preview`, `--clean [--yes]`, `--report`, bare `--json` (maps to `scan --json` — the old menu bar app contract), `--category`, `--config-show/--config-enable/--config-disable`, `--install-deps`; also aliases `preview`→`scan`, `history`→`report`. Existing cron jobs, shell aliases, and the v1 app keep working — don't break these.

Categories (20): `xcode`, `docker`, `node`, `python`, `caches`, `logs`, `homebrew`, `go`, `rust`, `ruby`, `cocoapods`, `gradle`, `maven`, `ai`, `ide`, `browsers`, `system`, `flutter`, `php`, `vms`

### Tests
```bash
python3 -m unittest discover -s tests    # 136 tests, stdlib only, no deps
```
CI runs tests + smoke tests + the app build on `macos-latest`.

### Install & Schedule
```bash
bash install.sh                        # Copies to ~/mac-cleaner/, adds aliases, installs the app
~/mac-cleaner/scheduler.sh weekly|monthly|remove|status
```

`scheduler.sh weekly|monthly` installs two launchd agents (`com.fullex.maccleaner.clean` — `StartCalendarInterval`, runs `clean --yes --notify`; `com.fullex.maccleaner.diskwatch` — `StartInterval` 3600s, runs `disk-check`), replacing the old cron-based scheduling; both agents get an explicit `EnvironmentVariables.PATH` (Homebrew + standard dirs — the same list `CleanerBridge.runEngine` uses) so cmd-based targets don't silently no-op under launchd's minimal default PATH. launchd catches up on a missed calendar run after sleep/wake, unlike cron. `status` calls `launchctl list <label>` per agent, so it distinguishes "loaded", "plist present but not loaded", and "not installed" instead of trusting a plist's mere existence; `remove` unloads both agents. An existing cron line referencing `mac-cleaner/cleaner.py` (not an unanchored `cleaner.py` match, which could catch an unrelated user script) is stripped the first time `weekly`/`monthly` runs; the cadence actually installed is always the one you invoked, never the old cron line's own cadence, so migrating installs exactly once. `doctor`'s Schedule check makes the same `launchctl list` distinction and flags a leftover legacy cron entry.

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
- `MacCleanerApp.swift` — `MenuBarExtra` (reclaimable size, "Last cleaned", Scan, Auto-Clean Safe, Open, Quit; `LSUIElement`, no Dock icon) + window with 4 tabs
- `DashboardView.swift` — grouped targets with checkboxes, in-app clean via `clean --targets … --yes --json`
- `ProjectsView.swift`, `HistoryView.swift`, `SettingsView.swift` — Settings persists through `config` subcommands so CLI and app share one config, incl. the notification/low-disk-alert toggles
- `NotificationManager.swift` — posts native notifications after an in-app clean; is its own `UNUserNotificationCenterDelegate` so banners still show while the app is frontmost (the default suppresses them); degrades silently if the user denies permission

### Python cleaner.py internals
- `get_targets(config)` — 70+ targets across 20 categories. Each has a **stable kebab-case `id`** (e.g. `xcode-derived-data`, `npm-cache`), `label`, `description`, `safe` bool, and either a `path` (glob patterns with `*` supported), a `cmd` (docker prune, brew cleanup, …), or `empty_only=True` (delete contents, keep dir — used for `~/Library/Caches` and `~/.Trash`)
- `measure_targets(targets)` — parallel `du -sk` via `ThreadPoolExecutor`; cmd targets use `estimate_cmd` parsers
- `delete_target(t, mode)` — refuses anything outside `$HOME` (and `$HOME` itself / `/`), never follows symlinks (unlinks them); `mode="trash"` moves to `~/.Trash` instead (the `trash` target always hard-deletes)
- `run_dry_run(targets, mode, json_mode)` — resolves exactly what a real `clean` would delete (concrete paths + sizes, or the command that would run) without deleting anything or writing to `report.log`/`snapshots.log`; used by `clean --dry-run` and `projects --dry-run`
- `scan_projects(config)` — walks `project_roots` to bounded depth for artifact dirs (`node_modules`, `.venv`, `target`, `build`, `Pods`, `.next`, …) requiring a sibling manifest (`package.json`, `Cargo.toml`, `pyproject.toml`, …) and min age (default 30 days); never descends into artifacts. When `project_git_check` is on (default), each hit's project dir is also checked with `git` for uncommitted/unpushed work (`_git_info`); dirty/unpushed projects are excluded from `projects --clean --yes` unless named via `--targets`
- `_notify(title, message)` — posts a macOS notification via `osascript`; never raises, warns on stderr and returns `False` on failure so a notification problem never fails a clean. Used by `clean --notify` (honours config `notifications`) and `run_disk_check`
- `run_disk_check(config, json_mode)` — one `shutil.disk_usage` call, no measurement or snapshot; warns via `_notify` when free space is below `low_disk_threshold_gb` (default 10 GB, falls back with a stderr warning if malformed), throttled to once per 24h via `alerts.json`; **always exits 0**
- `run_doctor(config)` — checks python, rich, config validity, install, schedule (launchd agents + legacy cron), app, tool availability, disk
- `run_tui_clean(targets)` — curses checklist; falls back to y/N prompts when non-interactive
- `translate_legacy(argv)` — v1 flag → v2 subcommand shim, runs before argparse
- `report.log` (sibling to `cleaner.py`, falling back to `~/Library/Application Support/MacCleaner/` when that directory isn't writable, or when it's inside a `.app` bundle regardless of writability — e.g. the app's bundled fallback engine running from inside its signed `.app`) stores the last 50 runs as JSON
- `snapshots.log` (same location rule as `report.log`; env override `MACCLEANER_SNAPSHOTS`) — every `scan` and real `clean`/`projects --clean` run (not `--dry-run`) appends a disk-usage snapshot, capped at the last 365 entries; a snapshot on the same calendar day as the previous one replaces it instead of appending, so 365 entries covers roughly a year. `report` prints a disk trend and `report --json` gains a `disk_history` key
- `alerts.json` (same location rule as `report.log`; env override `MACCLEANER_ALERTS`) — `disk-check`'s low-disk throttle state (last-notified timestamp), so a warning fires on the above→below transition and at most once per 24h after that
- Both `report.log` and `snapshots.log` writes are atomic (dump to a temp file, then `os.replace()`) so two concurrent runs (e.g. a launchd `clean --yes` overlapping a menu bar app scan) can't corrupt either file

### Safe vs. Review distinction
Each target has a `safe` bool. `--yes` / `auto_approve` only cleans `safe=True` targets. Review targets (`safe=False` — Xcode Archives, AI models, iOS backups, Trash, …) need explicit selection (`--targets id --yes` counts as consent) or interactive confirmation.

### Config
`config.json` (sibling to `cleaner.py`; installed: `~/mac-cleaner/config.json`) — missing keys merge with `DEFAULT_CONFIG` at load. Keys: `enabled_categories`, `skip_paths`, `log_threshold_mb`, `auto_approve`, `schedule`, `delete_mode` (`"rm"` | `"trash"`), `project_roots`, `project_min_age_days`, `project_git_check` (default `true`; disables the git dirty/unpushed check in `projects` when set `false`), `notifications` (default `true`; gates `clean --notify` and app notifications), `low_disk_alerts` (default `true`; gates `disk-check`'s warning — independent of `notifications`), `low_disk_threshold_gb` (default `10`), `full_refresh_hours` (default `6`; app-side only, the engine never reads it — how often the app runs a full `scan` between its 60s `report` ticks).

### Env vars
- `MACCLEANER_CONFIG` / `MACCLEANER_LOG` / `MACCLEANER_SNAPSHOTS` / `MACCLEANER_ALERTS` — override config/log/snapshots/alerts paths (used by tests); these always win over the beside-`cleaner.py`-or-Application-Support default resolution
- `MACCLEANER_ENGINE` — app-side override of the engine path (app development)

## Install Path vs. Source
Source lives in this repo; installed copy lives at `~/mac-cleaner/`, app at `~/Applications/MacCleaner.app`. When testing changes, either re-run `install.sh` or call `cleaner.py` directly from the repo path (for the app, set `MACCLEANER_ENGINE` to the repo's `cleaner.py`).

## Optional Dependency
`rich` is optional — detected at import, falls back to plain text (`RICH = False`). All output paths have both rich and plain variants.

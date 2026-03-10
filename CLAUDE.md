# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

MacCleaner is a macOS developer storage cleanup tool with two components:
1. **`cleaner.py`** — Python CLI (the core engine for all scan/delete logic)
2. **`AppDelegate.swift`** — Swift menu bar app (a thin wrapper that calls the Python CLI)

## Commands

### CLI (after install)
```bash
python3 cleaner.py               # No args = show welcome/help screen
python3 cleaner.py --preview     # Dry run: show what would be deleted + sizes
python3 cleaner.py --clean       # Interactive cleanup (TUI checklist via curses)
python3 cleaner.py --clean --yes # Auto-approve all safe items (cron mode)
python3 cleaner.py --report      # Show last 10 cleanup runs from report.log
python3 cleaner.py --json        # JSON output (consumed by menu bar app)
python3 cleaner.py --install-deps # Install 'rich' for pretty terminal output
python3 cleaner.py --category xcode  # Scope run to one category only
```

### Config Management
```bash
python3 cleaner.py --config-show              # Print current config.json
python3 cleaner.py --config-enable docker     # Enable a category
python3 cleaner.py --config-disable ruby      # Disable a category
```

Available categories: `xcode`, `docker`, `node`, `python`, `caches`, `logs`, `homebrew`, `go`, `rust`, `ruby`, `cocoapods`, `gradle`, `maven`

### Install & Schedule
```bash
bash install.sh                        # Copies to ~/mac-cleaner/, adds aliases, installs rich
~/mac-cleaner/scheduler.sh weekly      # Cron: every Monday 9am
~/mac-cleaner/scheduler.sh monthly     # Cron: 1st of month
~/mac-cleaner/scheduler.sh remove      # Remove cron job
~/mac-cleaner/scheduler.sh status      # Check current schedule
```

After install, shell aliases are added to `~/.zshrc`: `maccleaner`, `mclean`, `mpreview`, `mreport`.

### Menu Bar App
Open `AppDelegate.swift` as a new macOS app target in Xcode, build & run. No dock icon (`.accessory` activation policy).

## Architecture

### Python ↔ Swift Bridge
The Swift app has **no cleaning logic** of its own. It communicates with `cleaner.py` in two ways:
- **Background data**: calls `python3 cleaner.py --json` via `Process()`, decodes stdout into `CleanerReport` (a `Codable` struct) to update the menu bar display
- **Interactive actions**: opens Terminal via `NSAppleScript` and runs `cleaner.py --preview` or `--clean [--yes]` there

The JSON schema (`CleanerReport` / `CleanerTarget`) is the contract between the two layers. Changes to the `--json` output format must be mirrored in the Swift structs.

### Python cleaner.py internals
- `get_targets(config)` — builds the list of things to potentially clean (path-based or command-based)
- `measure_targets(targets)` — uses `du -sk` subprocess to calculate sizes
- `delete_target(t)` — either `shutil.rmtree` for path targets, or `subprocess.run(shell=True)` for command-based targets (docker prune, pnpm store prune, etc.)
- `run_tui_clean(targets)` — curses-based checklist UI for `--clean`; automatically falls back to y/N prompt loop if terminal is non-interactive (cron, piped output)
- `report.log` (sibling to `cleaner.py`) stores the last 50 run entries as JSON

### Safe vs. Review distinction
Each target has a `safe` bool. `--yes` / `auto_approve` only deletes `safe=True` targets. Review targets (`safe=False`) always require confirmation: Xcode Archives, pyenv shims, general `~/Library/Caches`.

### Config
`config.json` (sibling to `cleaner.py`) controls which categories run, paths to skip, log threshold, and auto-approve default. Installed to `~/mac-cleaner/config.json`. The Python script merges missing keys with `DEFAULT_CONFIG` at load time.

## Install Path vs. Source
Source lives in this repo; installed copy lives at `~/mac-cleaner/`. The Swift app's `cleanerPath` is hardcoded to `~/mac-cleaner/cleaner.py`. When testing changes during development, either re-run `install.sh` or call `cleaner.py` directly from the repo path.

## Optional Dependency
`rich` is optional — the script detects it at import and falls back to plain text output (`RICH = False`). All output paths have both rich and plain variants.

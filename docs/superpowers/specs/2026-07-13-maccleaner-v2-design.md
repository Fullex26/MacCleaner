# MacCleaner v2.0 — Design

**Date:** 2026-07-13
**Status:** Approved (autonomous session — decisions made per maintainer's full-autonomy directive)

## Goal

Make MacCleaner substantially more useful and easier to use for two audiences:

1. **Humans** — a real macOS app (dashboard + menu bar), richer CLI, more cleanup coverage, safer deletes.
2. **AI agents** — a fully machine-operable CLI: JSON on every command, stable target IDs, documented exit codes, and an `AGENTS.md` contract.

Python stays the engine (single-file, stdlib-only). Swift stays a thin client over the `--json` contract.

## Approaches considered

### CLI evolution
- **A. More flags on the flat interface** — least change, but `--projects-min-age-days` style flags scale badly.
- **B. Clean-break subcommand rewrite** — breaks every installed cron job, alias, and the Swift app.
- **C. Subcommands primary + legacy flag shim (chosen)** — v1.2 already shims `clean` → `--clean`; invert it. `scan`, `clean`, `projects`, `report`, `doctor`, `config`, `categories` become argparse subparsers; the old `--preview/--clean/--json/--config-*` spellings are translated pre-parse. Zero breakage, modern surface.

### macOS app
- **A. Enhance the AppKit menu-bar-only app** — stays a launcher that shells out to Terminal; not a "real app".
- **B. SwiftUI app: MenuBarExtra + dashboard window (chosen)** — scan table with checkboxes, clean runs *in-app* via `Process` + `clean --targets … --yes --json` (no Terminal hop), history and settings views. Built by `app/build.sh` with `swiftc` — no `.xcodeproj` needed, contributors build from source with one command. Bundles `cleaner.py` in `Contents/Resources` as a fallback engine so the app works without `install.sh`.
- **C. Full native Swift engine rewrite** — ROADMAP Phase 5; out of scope. The Python engine is the tested core and the agent interface.

### Agent interface
No competing approaches — the known-good pattern: JSON everywhere, stable IDs, stdout=data / stderr=messages, documented exit codes (0 ok, 1 runtime error, 2 usage error), `AGENTS.md` describing the contract.

## CLI v2 surface

```
maccleaner                       # welcome/help
maccleaner scan     [--category C]... [--min-size MB] [--json]
maccleaner clean    [--yes] [--targets id,id] [--category C]... [--min-size MB] [--trash] [--json]
maccleaner projects [--roots P]... [--min-age-days N] [--clean [--yes] [--targets id,id]] [--json]
maccleaner report   [-n N] [--json]
maccleaner doctor   [--json]
maccleaner config   show | path | enable C | disable C | set KEY VALUE
maccleaner categories [--json]
```

Legacy spellings (`--preview`, `--clean --yes`, `--report`, `--json`, `--category`, `--config-show/enable/disable`, `--install-deps`) keep working via pre-parse translation. The bare `--json` flag maps to `scan --json` (the menu-bar-app contract).

### Target model
Each target gains a **stable kebab-case `id`** (e.g. `xcode-derived-data`, `npm-cache`) and a one-line `description`. IDs are the agent-facing selector (`clean --targets npm-cache,pip-cache`). JSON scan output becomes a superset of the v1 schema (adds `id`, `description`, `exists`) so the already-installed Swift app keeps decoding it.

### New coverage (curated)
- **xcode**: SwiftPM cache, Carthage cache, Xcode cache, CoreSimulator caches
- **node**: bun cache, deno cache, npx cache, node-gyp cache
- **python**: uv cache, poetry cache, ruff cache
- **ai** (new): Hugging Face hub (review), PyTorch hub (review), Ollama models (review)
- **ide** (new): VS Code Cache/CachedData, JetBrains caches
- **browsers** (new): Arc, Brave, Edge, Firefox profile caches (glob paths)
- **caches**: Slack, Discord, Playwright (review), Puppeteer (review), Electron
- **system** (new): Empty Trash (review), iOS device backups (review)

Glob support: a target path containing `*` measures/deletes all matches. Existing v1 targets keep their category and `safe` flag; existing user configs simply don't have the new categories enabled until the user enables them (fresh installs get all).

### `projects` — stale build-artifact finder
Scans configurable roots (default `~/Documents`, `~/Developer`, `~/Projects`, `~/Code`, `~/dev`; config key `project_roots`) to bounded depth for artifact dirs with a sibling manifest proving what they are: `node_modules`+`package.json`, `.venv|venv`+`pyproject.toml|requirements.txt|setup.py`, `target`+`Cargo.toml`, `build`+`build.gradle(.kts)`, `Pods`+`Podfile`, `.next|.nuxt|.turbo|.parcel-cache`+`package.json`, `.pytest_cache|.mypy_cache|.ruff_cache`. Staleness = artifact dir mtime older than `--min-age-days` (default 30, config `project_min_age_days`). Never descends into artifacts; never follows symlinks. `--clean` converts hits to review-level targets and reuses the normal clean pipeline.

### Safety hardening
- `delete_target` refuses: `$HOME` itself, `/`, anything not under `$HOME`, and symlinks (unlinked, never traversed).
- **Trash mode**: `--trash` or config `delete_mode: "trash"` moves to `~/.Trash/<name>-<timestamp>` instead of `rm` (recoverable; space freed on Trash empty). The Empty-Trash target always hard-deletes.
- `doctor` diagnoses: Python version, rich availability, optional tool presence (brew/docker/xcrun/…), config validity, install/cron/app status.

### Output discipline
In `--json` mode: JSON on stdout, everything human on stderr. Exit codes: 0 success, 1 runtime error, 2 usage error.

## macOS app (SwiftUI, macOS 13+)

```
app/
  Sources/           MacCleanerApp.swift  CleanerBridge.swift
                     DashboardView.swift  HistoryView.swift  SettingsView.swift
  Info.plist
  build.sh           # swiftc → MacCleaner.app (arm64 + x86_64 lipo when possible)
```

- **MenuBarExtra**: reclaimable size in the bar; menu with Scan, Auto-Clean Safe (in-app, with confirmation + notification), Open Dashboard, Quit. `LSUIElement` — no Dock icon.
- **Dashboard**: disk-usage header, category-grouped target table with checkboxes (safe pre-checked), Clean Selected → `clean --targets … --yes --json` via `Process`, per-item results.
- **History**: renders `report --json`. **Settings**: category toggles, delete mode, refresh interval — all persisted through `config` subcommands so CLI and app share one config.
- **Engine resolution**: prefer `~/mac-cleaner/cleaner.py`, fall back to bundled `Contents/Resources/cleaner.py`.
- Replaces root-level `AppDelegate.swift` (git history preserves it). The committed pre-built `MacCleaner.app` is rebuilt from the new source.

## Tests & CI

`tests/test_cleaner.py` (stdlib `unittest`, no deps): size formatting, config merge, ID uniqueness/format, filters, delete safety guards (tmpdir + fake HOME), trash mode, projects scanner on a synthetic tree, JSON schema keys, legacy-flag smoke tests via subprocess. CI runs `python3 -m unittest` on `macos-latest` alongside existing smoke tests.

## Docs

README overhaul (humans + agents sections), **AGENTS.md** (machine contract: commands, JSON shapes, IDs, exit codes), CHANGELOG `2.0.0`, ROADMAP updates, CLAUDE.md sync, install.sh updates (installs app, keeps aliases).

## Error handling

Engine: every subprocess call already wrapped with timeouts/fallbacks — preserved. New code paths return structured errors in JSON (`{"error": …}` per item) rather than crashing mid-clean; a failed item never aborts the run. App: decode failures surface as a status message, never a crash; `Process` failures show the CLI's stderr.

## Out of scope (unchanged from ROADMAP)

Code signing/notarization, Homebrew cask, Swift engine rewrite, App Store.

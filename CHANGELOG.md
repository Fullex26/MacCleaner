# Changelog

All notable changes to MacCleaner will be documented here.

Format: [Keep a Changelog](https://keepachangelog.com/en/1.0.0/)
Versioning: [Semantic Versioning](https://semver.org/spec/v2.0.0.html)

---

## [2.1.0] — Unreleased

### Added
- 17 new cleanup targets across 3 new categories — `flutter` (Dart pub cache), `php` (Composer), `vms` (Colima, Vagrant, minikube) — plus yarn classic cache, npm logs, conda clean, sccache, LM Studio & Whisper models, Xcode DocumentationCache, Cypress, MS Teams, Zoom updater, Terraform plugins, and Expo caches
- Disk snapshots: every scan/clean records free space + reclaimable to `snapshots.log` (365 hourly-deduped entries); `report` shows a disk trend and `report --json` gains `disk_history`
- Git-aware `projects`: dirty or unpushed repos are badged and excluded from `--yes` sweeps (config `project_git_check`)
- `clean --dry-run` / `projects --dry-run`: exact resolved paths + sizes, zero side effects

---

## [2.0.0] — 2026-07-14

Backward-compatible with all v1 interfaces — existing cron jobs, shell aliases, and the old menu bar app keep working. The major bump reflects the scope of the new surface, not breakage.

### Added
- **Subcommand CLI** — `scan`, `clean`, `projects`, `report`, `doctor`, `config` (`show`/`path`/`enable`/`disable`/`set`), `categories`, `install-deps`; plus `preview` → `scan` and `history` → `report` aliases. All v1 spellings (`--preview`, `--clean [--yes]`, `--report`, bare `--json`, `--category`, `--config-show`/`--config-enable`/`--config-disable`, `--install-deps`) are translated pre-parse and keep working
- **Stable target IDs** — every target has a kebab-case `id` (e.g. `xcode-derived-data`, `npm-cache`, `huggingface-hub`); `clean --targets ID,ID` cleans exactly those. `--targets` + `--yes` counts as explicit consent for Review items
- **`--json` on every data command** — data on stdout, human messages on stderr; exit codes: 0 success, 1 runtime error, 2 usage error. Scan JSON is a superset of the v1 schema, so existing consumers keep decoding it
- **25+ new targets, 4 new categories** — `ai` (Hugging Face hub, PyTorch hub, Ollama models), `ide` (VS Code, JetBrains), `browsers` (Arc, Brave, Edge, Firefox), `system` (Trash, iOS device backups); plus SwiftPM/Carthage/CoreSimulator, bun/deno/npx/node-gyp, uv/poetry/ruff, and Slack/Discord/Playwright/Puppeteer/Electron caches. Now 60+ targets across 17 categories
- **`--min-size MB`** filter on `scan` and `clean`; **`scan --all`** shows empty/not-installed targets too
- **`projects` command** — finds stale build artifacts (`node_modules`, `.venv`/`venv`, `target`, `build`, `Pods`, `.next`, `.nuxt`, `.turbo`, `.parcel-cache`, `.pytest_cache`, `.mypy_cache`, `.ruff_cache`) under configurable roots (default `~/Documents`, `~/Developer`, `~/Projects`, `~/Code`, `~/dev`); requires a sibling manifest (`package.json`, `Cargo.toml`, `pyproject.toml`, …) and a minimum age (default 30 days). `--clean` feeds hits into the normal clean pipeline
- **`doctor` command** — environment health check: Python version, `rich`, config validity, install, cron, app, tool availability, disk space
- **Trash mode** — `--trash` flag or config `delete_mode: "trash"` moves items to `~/.Trash` instead of deleting (recoverable; space freed when Trash is emptied)
- **Safety hardening** — deletes refuse anything outside `$HOME` (and `$HOME` itself); symlinks are unlinked, never followed; `~/Library/Caches` and `~/.Trash` are emptied (contents only), never removed themselves
- **Parallel measurement** — target sizes measured concurrently (thread pool), so scans are much faster
- **SwiftUI app rewrite** (`app/`, macOS 13+) — menu bar extra (reclaimable size, Scan, Auto-Clean Safe, Open, Quit) plus a dashboard window with 4 tabs: Dashboard (category-grouped targets with checkboxes, cleans in-app — no Terminal hop), Projects, History, and Settings (category toggles + delete mode, shared with the CLI config). Build with `bash app/build.sh`: `swiftc`, universal arm64 + x86_64, ad-hoc signed, bundles `cleaner.py` as a fallback engine; `--install` copies to `~/Applications`
- **Test suite + CI** — `tests/` (39 tests, stdlib `unittest`, no deps: `python3 -m unittest discover -s tests`); CI runs the suite, CLI smoke tests, and the app build on `macos-latest`
- **`AGENTS.md`** — machine contract for AI agents: commands, JSON shapes, target IDs, exit codes
- **Env overrides** — `MACCLEANER_CONFIG` and `MACCLEANER_LOG` (engine), `MACCLEANER_ENGINE` (app development)
- **New config keys** — `delete_mode`, `project_roots`, `project_min_age_days`, `schedule`

### Removed
- **`AppDelegate.swift`** — the AppKit menu-bar launcher is replaced by the SwiftUI app in `app/` (git history preserves it)

---

## [1.2.1] — 2026-03-10

### Added
- `SECURITY.md` — private vulnerability reporting via GitHub's advisory flow

### Changed
- `CLAUDE.md`: corrected no-args default description; added `maccleaner` alias to install list
- Dependabot already configured for GitHub Actions (weekly, Monday)
- Branch protection on `main` now requires CI to pass before merging

---

## [1.2.0] — 2026-03-05

### Added
- **`--version`** — prints `MacCleaner 1.2.0` and exits
- **`--category CATEGORY`** — filter any mode (`--preview`, `--clean`, `--json`) to a single category (e.g. `--preview --category xcode`)
- **`--config-show`** — prints current `config.json` as formatted JSON
- **`--config-enable CATEGORY` / `--config-disable CATEGORY`** — toggle categories in `config.json` from the CLI
- **Interactive TUI checklist** — `--clean` now opens an arrow-key/space-bar checklist (curses); falls back to y/N prompts when not a real TTY (pipe, cron, CI)
- **Size estimates for cmd-based targets** — Homebrew cache and Docker now show real reclaimable sizes before cleaning (using dry-run commands); pnpm shows `~unknown` if not installed
- **`brew autoremove`** — new Homebrew target removes unused dependencies

### Changed
- Targets with no path and no estimate command now show `cmd-based`; targets with an estimate command that returned nothing show `~unknown`

---

## [1.1.0] — 2026-03-03

### Added
- **Homebrew cache** — `brew cleanup --prune=all` (often 1–5 GB, cmd-based)
- **Go module cache** — `~/go/pkg/mod`
- **Go build cache** — `~/go/pkg/cache`
- **Cargo registry** — `~/.cargo/registry`
- **Cargo git cache** — `~/.cargo/git`
- **Ruby gem cleanup** — `gem cleanup` for stale gem versions (cmd-based)
- **CocoaPods cache** — `~/Library/Caches/CocoaPods`
- **Gradle caches** — `~/.gradle/caches`
- **Maven local repo** — `~/.m2/repository` (marked Review — forces full re-download on delete)

---

## [1.0.0] — 2026-03-01

### Added
- Python CLI (`cleaner.py`) with `--preview`, `--clean`, `--clean --yes`, `--report`, `--json` modes
- Cleanup categories: Xcode DerivedData/Previews/DeviceSupport/Archives, Docker, Node (npm/pnpm/yarn), Python pip, app caches, oversized logs
- Safe vs. Review item distinction — auto-clean only touches safe items
- `config.json` for per-user configuration (categories, skip paths, log threshold, auto-approve)
- `install.sh` — one-command installer to `~/mac-cleaner/` with shell aliases
- `scheduler.sh` — cron scheduling (weekly / monthly)
- `report.log` — stores last 50 run summaries as JSON
- Swift menu bar app (`AppDelegate.swift`) — shows reclaimable space, launches CLI in Terminal
- `MacCleaner.app` — pre-built arm64 bundle, no Dock icon (`LSUIElement`)
- Optional `rich` dependency for pretty terminal output, with plain-text fallback

### Fixed
- `Path(None)` crash when a target path resolved to `None`
- `.app` bundle now copied to `~/Applications/` by installer for persistence

# Changelog

All notable changes to MacCleaner will be documented here.

Format: [Keep a Changelog](https://keepachangelog.com/en/1.0.0/)
Versioning: [Semantic Versioning](https://semver.org/spec/v2.0.0.html)

---

## [Unreleased]

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

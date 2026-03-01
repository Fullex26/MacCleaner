# Changelog

All notable changes to MacCleaner will be documented here.

Format: [Keep a Changelog](https://keepachangelog.com/en/1.0.0/)
Versioning: [Semantic Versioning](https://semver.org/spec/v2.0.0.html)

---

## [Unreleased]

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

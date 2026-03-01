# MacCleaner Roadmap

This document tracks planned features and long-term direction. Community input welcome — open an issue or PR to discuss anything here.

---

## Current State — v1.0 ✅

- Python CLI with rich terminal output
- Cleans: Xcode DerivedData/Previews/DeviceSupport, Docker, Node (npm/pnpm/yarn), Python pip, app caches, oversized logs
- Safe vs. Review distinction — auto-clean only touches safe items
- `--preview`, `--clean`, `--clean --yes`, `--report`, `--json` modes
- Cron scheduling via `scheduler.sh`
- Menu bar app (Swift, arm64) showing reclaimable space
- `install.sh` — one-command install to `~/mac-cleaner/`

---

## Phase 1 — More Coverage (Quick Wins)

> Low effort, high impact. Targets any developer is likely to have.

- [ ] **Homebrew cache** — `brew cleanup --prune=all` (often 1–5 GB)
- [ ] **Go module cache** — `~/go/pkg/mod` and `~/go/pkg/cache`
- [ ] **Cargo / Rust** — `~/.cargo/registry` and `~/.cargo/git`
- [ ] **Ruby gems** — `gem cleanup` for stale gem versions
- [ ] **CocoaPods cache** — `~/Library/Caches/CocoaPods`
- [ ] **Gradle / Android** — `~/.gradle/caches`
- [ ] **Maven** — `~/.m2/repository` for unused snapshots
- [ ] **iOS Simulator runtimes** — remove unused simulator versions via `xcrun simctl`)
- [ ] **Xcode device symbols** — `~/Library/Developer/Xcode/iOS DeviceSupport` (often 10–30 GB)

---

## Phase 2 — Smarter Menu Bar App

> Makes the menu bar app genuinely useful day-to-day, not just a launcher.

- [ ] **Live free disk space** — show current free GB in menu title, not just reclaimable
- [ ] **Low disk alerts** — macOS notification when free space drops below a configurable threshold (e.g. 10 GB)
- [ ] **Auto-refresh** — poll every N minutes instead of only on app launch
- [ ] **Preferences panel** — toggle categories, set thresholds, configure schedule — without editing `config.json` by hand
- [ ] **Last cleaned timestamp** — show "Last cleaned: 3 days ago" in menu
- [ ] **Per-category breakdown** — submenu showing size per category before cleaning

---

## Phase 3 — Distribution & Trust

> Required before sharing with non-developers or putting on GitHub Releases.

- [ ] **Code signing** — sign the `.app` with an Apple Developer certificate
- [ ] **Notarization** — submit to Apple's notarization service so Gatekeeper allows it
- [ ] **Xcode project** — create a proper `.xcodeproj` so the app is buildable from source by contributors
- [ ] **GitHub Actions release build** — CI builds and signs the `.app` on version tags, attaches to GitHub Release
- [ ] **Homebrew Cask** — `brew install --cask maccleaner` for one-command install without cloning the repo

---

## Phase 4 — Native Swift Rewrite

> Eliminates the Python dependency, making the tool accessible to all Mac users.

- [ ] **Full Swift rewrite of cleaner engine** — replace `cleaner.py` with Swift, removing Python 3 requirement
- [ ] **SwiftUI preferences window** — replace JSON config editing with a proper settings UI
- [ ] **Sandboxed App Store build** — adapt for Mac App Store sandbox requirements
- [ ] **Sparkle auto-updater** — installed copies update themselves when new versions ship
- [ ] **Universal binary** — support both Apple Silicon and Intel

---

## Phase 5 — App Store & Beyond

> Long-term, if the project gains traction.

- [ ] **Mac App Store release** — requires full native rewrite + sandboxing + Apple Developer account
- [ ] **iCloud sync for config** — sync preferences across multiple Macs
- [ ] **Usage analytics (opt-in)** — understand which cleanup categories are most valuable
- [ ] **Scheduled scan reports** — weekly email/notification summarizing what was cleaned

---

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) to get started. The easiest first contributions are adding new cleanup targets in `cleaner.py` — each one is a small dictionary entry in `get_targets()`.

New cleanup target ideas are tracked in [issues](../../issues) with the `cleanup-target` label.

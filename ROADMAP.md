# MacCleaner Roadmap

This document tracks planned features and long-term direction. Community input welcome — open an issue or PR to discuss anything here.

---

## Current State — v2.0 ✅

- Subcommand CLI — `scan`, `clean`, `projects`, `report`, `doctor`, `config`, `categories` — with every v1 spelling (`--preview`, `--clean --yes`, `--report`, `--json`, `--config-*`) still working, so existing cron jobs, aliases, and the old menu bar app don't break
- Agent-ready interface — `--json` on every data command, stable kebab-case target IDs (`clean --targets npm-cache,pip-cache`), JSON on stdout / messages on stderr, documented exit codes, `AGENTS.md` contract
- 60+ cleanup targets across 17 categories — new in v2.0: `ai` (Hugging Face, PyTorch, Ollama), `ide` (VS Code, JetBrains), `browsers` (Arc, Brave, Edge, Firefox), `system` (Trash, iOS device backups)
- `projects` — finds stale build artifacts (`node_modules`, `.venv`, `target`, `Pods`, `.next`, …) under your project roots, requiring a sibling manifest and minimum age before offering to delete
- Safety hardening — deletes only inside `$HOME`, never follows symlinks, empty-only semantics for `~/Library/Caches` and Trash, optional trash mode (`--trash` / `delete_mode: "trash"`)
- `doctor` — environment and install health check
- SwiftUI app (macOS 13+) — menu bar extra plus a real dashboard window (Dashboard / Projects / History / Settings), cleaning in-app through the CLI's JSON interface instead of hopping to Terminal
- Buildable from source in one command — `bash app/build.sh` (swiftc, universal arm64 + x86_64, bundles `cleaner.py` as a fallback engine)
- 39 stdlib-only unit tests; CI runs tests, smoke tests, and the app build on `macos-latest`
- Still here from v1: safe vs. review distinction, cron scheduling via `scheduler.sh`, `install.sh`, optional `rich` output
- **v2.1 additions** — 17 more targets across 3 new categories (`flutter`, `php`, `vms`), now 70+ targets across 20 categories; disk-usage snapshots with a `report` trend view (`disk_history` in `report --json`); git-aware `projects` (dirty/unpushed repos excluded from `--yes` sweeps, config `project_git_check`); `--dry-run` on `clean`/`projects` for exact-path previews with zero side effects. 69 tests total

---

## Next — v2.x Ideas

> Incremental improvements on the v2.0 foundation. A few of these pull forward items from the phases below.

- [ ] **Shell completions** — zsh/bash completion for subcommands, categories, and target IDs
- [ ] **Notifications** — macOS notification when a scheduled clean finishes, plus low-disk alerts when free space drops below a configurable threshold
- [ ] **Sparkle auto-updater** — installed apps update themselves when new versions ship
- [ ] **Homebrew Cask** — `brew install --cask maccleaner` without cloning the repo
- [ ] **launchd instead of cron** — the native macOS scheduler; catches up on missed runs after sleep, no crontab editing
- [x] **More targets** — 17 added in v2.1: 3 new categories (`flutter`, `php`, `vms` — Dart/Composer/Colima/Vagrant/minikube) plus sccache, conda clean, Yarn classic cache, npm logs, LM Studio, Whisper, Xcode docs cache, Cypress, Teams, Zoom, Terraform, and Expo caches. Always room for more; open an issue with the `cleanup-target` label

---

## Phase 1 — More Coverage (Quick Wins) ✅

> Low effort, high impact. Targets any developer is likely to have.

- [x] **Homebrew cache** — `brew cleanup --prune=all` (often 1–5 GB)
- [x] **Go module cache** — `~/go/pkg/mod` and `~/go/pkg/cache`
- [x] **Cargo / Rust** — `~/.cargo/registry` and `~/.cargo/git`
- [x] **Ruby gems** — `gem cleanup` for stale gem versions
- [x] **CocoaPods cache** — `~/Library/Caches/CocoaPods`
- [x] **Gradle / Android** — `~/.gradle/caches`
- [x] **Maven** — `~/.m2/repository` for unused snapshots
- [x] **iOS Simulator runtimes** — `xcrun simctl delete unavailable` + `~/Library/Developer/CoreSimulator/Volumes` (Review)
- [x] **Xcode device symbols** — `~/Library/Developer/Xcode/iOS DeviceSupport` (often 10–30 GB)

---

## Phase 2 — CLI Polish ✅

> CLI-first, zero external dependencies beyond Python stdlib.

- [x] **`--version`** — `MacCleaner 2.0.0`
- [x] **`--category` filter** — scope any mode to one category
- [x] **Config CLI** — `config show/path/enable/disable/set` without editing JSON by hand
- [x] **Interactive TUI** — curses checklist with arrow keys + space toggle; graceful fallback
- [x] **Size estimates** — Homebrew and Docker show reclaimable bytes before cleaning
- [x] **`brew autoremove`** — remove unused Homebrew dependencies

---

## Phase 3 — Smarter Menu Bar App

> Makes the menu bar app genuinely useful day-to-day, not just a launcher. Largely landed in the v2.0 SwiftUI app.

- [x] **Live free disk space** — shown in the menu and the Dashboard header (the bar title still shows reclaimable)
- [ ] **Low disk alerts** — macOS notification when free space drops below a configurable threshold (e.g. 10 GB)
- [ ] **Auto-refresh** — poll every N minutes; the v2.0 app scans on launch and on demand only
- [x] **Preferences panel** — Settings tab toggles categories and delete mode, shared with the CLI's `config.json`; schedule is still configured via `scheduler.sh`
- [ ] **Last cleaned timestamp** — the History tab shows full run history, but the menu itself doesn't say "Last cleaned: 3 days ago" yet
- [x] **Per-category breakdown** — Dashboard groups targets by category with per-category size totals

---

## Phase 4 — Distribution & Trust

> Required before sharing with non-developers or putting on GitHub Releases.

- [ ] **Code signing** — sign the `.app` with an Apple Developer certificate (`build.sh` currently ad-hoc signs)
- [ ] **Notarization** — submit to Apple's notarization service so Gatekeeper allows it
- [x] **Buildable from source** — `app/build.sh` builds the app with plain `swiftc`, no `.xcodeproj` needed; contributors run one command
- [x] **GitHub Actions release build** — CI packages the `.app` and a CLI tarball on version tags and attaches them to the GitHub Release; artifacts stay unsigned until the two items above land
- [ ] **Homebrew Cask** — `brew install --cask maccleaner` for one-command install without cloning the repo

---

## Phase 5 — Native Swift Rewrite

> Eliminates the Python dependency, making the tool accessible to all Mac users.

- [ ] **Full Swift rewrite of cleaner engine** — replace `cleaner.py` with Swift, removing Python 3 requirement. Deliberately not done in v2.0: the Python engine is the tested core and the agent interface; Swift stays a thin client
- [x] **SwiftUI preferences window** — Settings tab in the v2.0 app, persisted through the same `config.json` as the CLI
- [ ] **Sandboxed App Store build** — adapt for Mac App Store sandbox requirements
- [ ] **Sparkle auto-updater** — installed copies update themselves when new versions ship
- [x] **Universal binary** — `app/build.sh` compiles arm64 + x86_64 and lipos them together

---

## Phase 6 — App Store & Beyond

> Long-term, if the project gains traction.

- [ ] **Mac App Store release** — requires full native rewrite + sandboxing + Apple Developer account
- [ ] **iCloud sync for config** — sync preferences across multiple Macs
- [ ] **Usage analytics (opt-in)** — understand which cleanup categories are most valuable
- [ ] **Scheduled scan reports** — weekly email/notification summarizing what was cleaned

---

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) to get started. The easiest first contributions are adding new cleanup targets in `cleaner.py` — each one is a small dictionary entry in `get_targets()` with a stable `id`.

New cleanup target ideas are tracked in [issues](../../issues) with the `cleanup-target` label.

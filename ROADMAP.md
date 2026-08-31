# MacCleaner Roadmap

This document tracks planned features and long-term direction. Community input welcome — open an issue or PR to discuss anything here.

---

## Current State — v2.15.0 ✅

- Subcommand CLI — `scan`, `clean`, `projects`, `report`, `doctor`, `config`, `categories` — with every v1 spelling (`--preview`, `--clean --yes`, `--report`, `--json`, `--config-*`) still working, so existing cron jobs, aliases, and the old menu bar app don't break
- Agent-ready interface — `--json` on every data command, stable kebab-case target IDs (`clean --targets npm-cache,pip-cache`), JSON on stdout / messages on stderr, documented exit codes, `AGENTS.md` contract
- 60+ cleanup targets across 17 categories — new in v2.0: `ai` (Hugging Face, PyTorch, Ollama), `ide` (VS Code, JetBrains), `browsers` (Arc, Brave, Edge, Firefox), `system` (Trash, iOS device backups)
- `projects` — finds stale build artifacts (`node_modules`, `.venv`, `target`, `Pods`, `.next`, …) under your project roots, requiring a sibling manifest and minimum age before offering to delete
- Safety hardening — deletes only inside `$HOME`, never follows symlinks, empty-only semantics for `~/Library/Caches` and Trash, optional trash mode (`--trash` / `delete_mode: "trash"`)
- `doctor` — environment and install health check
- SwiftUI app (macOS 13+) — menu bar extra plus a real dashboard window (Dashboard / Projects / History / Settings), cleaning in-app through the CLI's JSON interface instead of hopping to Terminal
- Buildable from source in one command — `bash app/build.sh` (swiftc, universal arm64 + x86_64, bundles `cleaner.py` as a fallback engine)
- 39 stdlib-only unit tests; CI runs tests, smoke tests, and the app build on `macos-latest`
- Still here from v1: safe vs. review distinction, scheduling via `scheduler.sh` (launchd as of v2.2, cron before it), `install.sh`, optional `rich` output
- **v2.1 additions** — 17 more targets across 3 new categories (`flutter`, `php`, `vms`), now 70+ targets across 20 categories; disk-usage snapshots with a `report` trend view (`disk_history` in `report --json`); git-aware `projects` (dirty/unpushed repos excluded from `--yes` sweeps, config `project_git_check`); `--dry-run` on `clean`/`projects` for exact-path previews with zero side effects. 69 tests total
- **v2.2 additions** — launchd scheduling replaces cron (catches up on missed runs after sleep; existing cron schedules migrate automatically); notifications when a scheduled clean finishes (`clean --notify`) and after in-app cleans; `disk-check`, a cheap hourly low-disk watch (config `low_disk_alerts`, `low_disk_threshold_gb`, default 10 GB, throttled to once a day); a live menu bar with split-cadence refresh (60s light tick, longer full rescan, config `full_refresh_hours`) and a "Last cleaned" readout. 136 tests total
- **v2.3 additions** — `schedule status|weekly|monthly|off` subcommand makes scheduling first-class engine logic (`--json`, `MACCLEANER_LAUNCH_AGENTS_DIR` override); `scheduler.sh` and `doctor`'s Schedule check both now delegate to it; the app's Settings gained a Schedule section (Off/Weekly/Monthly) so scheduling no longer requires the terminal; the Dashboard gained a free-space trend chart (Swift Charts) built from `report --json`'s disk history; the app now has a proper icon. 182 tests total
- **v2.4 additions** — hand-written zsh/bash shell completions (`completions/`, no runtime dependency) for subcommands, flags, config keys, and live category/target IDs, installed by `install.sh` and shipped in the release CLI tarball, with a parser cross-reference test guarding against drift; release-time code signing and notarization in CI, gated on repository secrets so the workflow ships ad-hoc signed exactly as before when they're absent; a validated, in-repo Homebrew cask (`Casks/maccleaner.rb`) and a tracked release procedure (`docs/RELEASING.md`) — the public tap stays unpublished until a signed/notarized release exists (see Phase 4 below). 186 tests total
- **v2.5 additions** — two new dynamic scanners for AI-era mess: `/private/tmp` build-artifact detection (`tmp` category, content-classified never name-classified, gated by a minimum age, and the one narrow carve-out to the home-only delete rule) and stale iOS simulator devices/unused runtimes (`simulators` category, driven entirely through `xcrun simctl`); `codex-sessions`/`codex-archived-sessions` review targets for OpenAI Codex CLI conversation history; a category auto-enable migration (`known_categories`) so a category added in a new release shows up for existing installs automatically; a bundle-aware `CONFIG_PATH` fix. Now 22 categories, 80+ targets. Signed, notarized, and the public Homebrew tap went live (`brew install --cask Fullex26/tap/maccleaner`). 186 tests total (release published without new tests beyond 2.4)
- **v2.6 additions — "Glass & Sparkle"** — a full visual overhaul of the menu bar app: sidebar navigation, glass panels, monospaced sizes, per-category color dots, and a cyan accent, all driven from one design system so light/dark mode stay in sync; a rich menu bar popover (disk ring, reclaimable hero number, top-3 categories, one-click "Clean safe items") replaces the plain text menu; Select All/None on Projects and All/None/Safe-only on the Dashboard; live per-item progress during in-app cleans instead of a frozen UI until completion; Sparkle auto-updates — a daily check with an in-app release-notes prompt, "Check for Updates…" in Settings, and an EdDSA-signed appcast published with every release (Homebrew-cask users keep using `brew upgrade`); dynamic scanners are now skipped when a CLI selection can't include them, so targeted cleans no longer pay `simctl`/`/tmp`-walk latency they don't need. 241 tests total
- **v2.7 additions — "App Uninstaller"** — a new `leftovers` category that finds per-app data still sitting under `~/Library` after you dragged an app to the Trash. Detection is bundle-ID-precise, never fuzzy or name-based: five bundle-ID-keyed roots (`Caches`, `Preferences`, `Saved Application State`, `HTTPStorages`, `WebKit`) are scanned for reverse-DNS-shaped entries whose exact bundle ID matches no installed `.app` — enumerated from `/Applications`, `~/Applications`, and `/System/Applications` including one level inside vendor wrapper folders, then double-checked against Spotlight with a single batched `mdfind` query so deeply nested real installs (Adobe Creative Cloud, printer utilities, Steam-bundled games) are never mistaken for orphans. Apple's own domains, MacCleaner's own bundle ID, and strict sub-domains of installed apps are always excluded, and every hit stays review-only (`safe: false`) — an unattended `clean --yes` can never take one. New config key `app_leftover_min_age_days` (default 7) plus `MACCLEANER_INSTALLED_APPS_DIRS` / `MACCLEANER_LEFTOVER_LIBRARY_ROOT` test overrides. Now 23 categories
- **v2.8 additions** — three more cleanup targets (`xcodebuildmcp-workspaces` and `chrome-optimization-hint-cache`, both safe; `chrome-optimization-model-store`, review-only because Chrome tracks those models in a `Local State` index MacCleaner doesn't touch, so recovery is unverified), taking the static table to 83; plus two **advisory, report-only** `doctor` checks that account for disk space the tool deliberately refuses to reclaim: `Swap` (how much disk the swapfiles under `/System/Volumes/VM` actually consume, flagged at 8 GiB — an absolute disk-consumed threshold, not a used/total ratio, which is kept only as informational text) and `Held-open files` (deleted-but-still-open files ≥500 MB, deduped by device+inode, largest holders named). Neither reclaims a byte, neither becomes a target, and MacCleaner will never kill a process or touch the swapfiles. Both carry a new additive `"advisory": true` key in `doctor --json` and are excluded from the top-level `ok` aggregate, so `ok` keeps meaning "a MacCleaner-owned problem with a remedy" rather than "something is unhealthy but nothing can be done". 373 tests total
- **v2.8.1** — security fix: `_safe_to_delete()`, the single guard every delete across every category routes through, checked path containment lexically and never resolved symlinks, so a target reaching its destination through a symlinked *ancestor* directory (e.g. a glob match under a symlinked cache subdirectory) could be lexically inside `$HOME` while physically resolving outside it. Fixed by resolving the parent directory (not the leaf) before the containment check — the leaf stays unresolved on purpose, since unlinking a symlink whose own directory entry is inside `$HOME` is always safe even when it points elsewhere. Found proactively via adversarial review, not a reported incident; independently re-verified with a 48-scenario attack suite before shipping. 377 tests total
- **v2.10 additions** — ten new cleanup targets (83 → 93), each found by scanning a real working developer Mac rather than guessed from vendor docs: `chrome-http-cache`, `spotify-browser-cache`, `clang-module-cache`, `electron-updater-pending` (`caches`); `typescript-cache` (`node`); `rustup-downloads` (`rust`); `ollama-updates`, `codex-sparkle-updates` (`ai`); plus review-only `codex-runtimes` and `antigravity-browser-profile`. `add()` gained a `paths=[...]` multi-path form, introduced because the Spotify target had to name two specific subdirectories: its apparent cache root turned out to be the app's embedded-Chromium profile holding logins, cookies and the DRM module, which a `--yes` sweep would have destroyed. Two new tests generalise that catch to every target — no `safe` target may resolve to a directory containing `Login Data`/`Cookies`/`Local State`, and no two targets may share a label. 406 tests total
- **v2.9 additions** — a new `storage-insights` subcommand: a read-only scan of `~/Documents`, `~/Downloads`, and `~/Desktop` (overridable via `MACCLEANER_STORAGE_INSIGHTS_ROOTS`) for individually large files, 100 MB floor, top 50 by size, stat-only so it never opens file contents and never triggers an iCloud-eviction download/hang. Deliberately separate from the delete pipeline — no target, no category, untouched by `clean`/`--yes`/`--dry-run` — because "large" personal file isn't the same judgment as "safe, rebuildable cache." The app's Dashboard gained a matching "Large Files" panel with per-item Reveal in Finder, degrading gracefully on an engine that predates the subcommand. 395 tests total

- **v2.11–v2.14 additions** — the Storage sidebar section (a read-only whole-disk drill-down over `storage-map --json`, with Move to Trash via `FileManager.trashItem` rather than the engine's delete path); accurate sizing everywhere (`du -x` so mounted disk images are never double-counted; allocated-blocks sizing so sparse files like `Docker.raw` report ~10 GB, not their 1 TB apparent size); `reclaimable_total()` de-duplicating nested targets out of the headline; three advisory `doctor` checks (Swap, Held-open files, System temp, Docker disk image); `storage-insights` widened to six roots with bundle-aware sizing; nested `/tmp` build-tree detection inside task workspaces with the delete carve-out widened to exactly two levels so those targets are actually deletable; and a tri-state `launchctl` check (`load_state`) so an unreachable `launchctl` is reported as "could not verify" instead of a broken schedule
- **v2.15 additions** — the last three Phase 6 items land: `report --stats` (local-first usage analytics over `report.log` — nothing leaves the machine), `config sync on|off|status` (config in iCloud Drive behind a symlink, with `save_config` writing through it), and the weekly cleanup digest (the scheduled clean's notification now carries a trailing-7-day total, so the weekly run's notification is the weekly report); `tmp_min_age_days` default lowered 3 → 1 now that every tmp target is review-only; staged designs committed for the two remaining structural items (`docs/V3-SWIFT-ENGINE.md`, `docs/APP-STORE-FEASIBILITY.md`)
---

## Next — v2.x Ideas

> Incremental improvements on the v2.0 foundation. A few of these pull forward items from the phases below.

- [x] **Shell completions** — hand-written zsh/bash completion for subcommands, flags, config keys, and live category/target IDs (`completions/`), installed by `install.sh` and shipped in the CLI tarball
- [x] **Notifications** — macOS notification when a scheduled clean finishes (`clean --notify`) or an in-app clean completes, plus low-disk alerts (`disk-check`) when free space drops below a configurable threshold (default 10 GB, throttled to once a day)
- [x] **Sparkle auto-updater** — installed apps update themselves: a daily background check, an in-app prompt with release notes, "Check for Updates…" in Settings, and an EdDSA-signed appcast (`releases/latest/download/appcast.xml`) published by CI on every release
- [x] **Homebrew Cask** — `Casks/maccleaner.rb` is live at `Fullex26/homebrew-tap` (`brew install --cask Fullex26/tap/maccleaner`), now that releases are signed and notarized
- [x] **launchd instead of cron** — the native macOS scheduler; catches up on missed runs after sleep, no crontab editing. Existing cron schedules migrate automatically
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

## Phase 3 — Smarter Menu Bar App ✅

> Makes the menu bar app genuinely useful day-to-day, not just a launcher. Largely landed in the v2.0 SwiftUI app; completed in v2.2.

- [x] **Live free disk space** — shown in the menu and the Dashboard header (the bar title still shows reclaimable)
- [x] **Low disk alerts** — macOS notification when free space drops below a configurable threshold (default 10 GB), via the hourly `disk-check` launchd agent
- [x] **Auto-refresh** — split-cadence refresh: a light 60-second tick (`report --json`) plus a full rescan on a longer interval (`full_refresh_hours`, default 6), on wake, and when the menu opens
- [x] **Preferences panel** — Settings tab toggles categories and delete mode (shared with the CLI's `config.json`), and (as of v2.3) the cleanup schedule itself (Off/Weekly/Monthly), which lives in launchd plists managed via the CLI's `schedule` subcommand rather than in `config.json`
- [x] **Last cleaned timestamp** — the menu bar now shows "Last cleaned: 3 days ago" alongside reclaimable size
- [x] **Per-category breakdown** — Dashboard groups targets by category with per-category size totals
- [x] **Disk trend chart** — Dashboard shows free-space-over-time (Swift Charts) from the engine's daily disk snapshots, with the low-disk threshold as a rule line

---

## Phase 4 — Distribution & Trust

> Required before sharing with non-developers or putting on GitHub Releases.

- [x] **Code signing** — live since v2.5.0: `release.yml` re-signs with the Developer ID certificate (Synapse Labs Pty Ltd) on every `v*` tag, embedded Sparkle framework signed inside-out as of v2.6.0 (`docs/RELEASING.md` §1, §7); `build.sh` still always ad-hoc signs for local/dev builds
- [x] **Notarization** — live since v2.5.0: `release.yml` submits to `notarytool`, staples, and verifies with `spctl` automatically on every tagged release
- [x] **Buildable from source** — `app/build.sh` builds the app with plain `swiftc`, no `.xcodeproj` needed; contributors run one command
- [x] **GitHub Actions release build** — CI packages the `.app` and a CLI tarball on version tags and attaches them to the GitHub Release, now signed and notarized
- [x] **Homebrew Cask** — published: `brew install --cask Fullex26/tap/maccleaner` (the tap `Fullex26/homebrew-tap` went live with v2.5.0, once notarization unblocked it)
- [x] **Auto-updates** — Sparkle ships in v2.6.0: daily check + prompt, EdDSA-signed appcast generated per release (`docs/RELEASING.md` §7)

---

## Phase 5 — Native Swift Rewrite

> Eliminates the Python dependency, making the tool accessible to all Mac users.

- [ ] **Full Swift rewrite of cleaner engine** — **stages 1–2 landed**: contract fixtures (`tests/fixtures/`, regenerated-and-diffed by the suite) and the read-only `MacCleanerKit` (`swift/`, generated target table, CI-enforced Swift/Python parity via `tools/check_swift_parity.py`). Stages 3–5 (dual-engine soak in the app, guard-first deletion port, cutover) per `docs/V3-SWIFT-ENGINE.md` (contract fixtures → read-only `MacCleanerKit` → dual-engine soak → guard-first deletion port → cutover). Deliberately not attempted as one rewrite: the Python engine is the tested core, and parity for a deletion tool must be proved, not assumed
- [x] **SwiftUI preferences window** — Settings tab in the v2.0 app, persisted through the same `config.json` as the CLI
- [ ] **Sandboxed App Store build** — assessed in `docs/APP-STORE-FEASIBILITY.md`: the sandbox denies exactly what MacCleaner does, so a MAS variant would be the read-only storage X-ray (`storage-map`/`storage-insights`), not the cleaner. Blocked on the V3 Swift core plus a product decision, not on engineering effort
- [x] **Sparkle auto-updater** — shipped in v2.6.0, see Phase 4
- [x] **Universal binary** — `app/build.sh` compiles arm64 + x86_64 and lipos them together

---

## Phase 6 — App Store & Beyond

> Long-term, if the project gains traction.

- [ ] **Mac App Store release** — see `docs/APP-STORE-FEASIBILITY.md`; revisit after V3 stage 3, when a Swift read-only core exists and the MAS variant becomes a packaging exercise. Requires owner-level App Store Connect actions
- [x] **iCloud sync for config** — `config sync on|off|status` (v2.15.0): config.json lives in iCloud Drive behind a symlink, shared by the CLI, the app, and the launchd agents; `off` is local-only and leaves the iCloud copy for other Macs
- [x] **Usage analytics (opt-in)** — `report --stats` (v2.15.0), implemented local-first: aggregates this machine's own `report.log` by target and category to answer "which categories are most valuable" with nothing ever leaving the machine. A networked telemetry backend was deliberately not built
- [x] **Scheduled scan reports** — the weekly scheduled clean's completion notification now carries a trailing-7-day digest (v2.15.0) — the weekly run's notification is the weekly report, with no second agent and no plist change. Email was deliberately not built (no send infrastructure; notifications are the native channel)

---

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) to get started. The easiest first contributions are adding new cleanup targets in `cleaner.py` — each one is a small dictionary entry in `get_targets()` with a stable `id`.

New cleanup target ideas are tracked in [issues](../../issues) with the `cleanup-target` label.

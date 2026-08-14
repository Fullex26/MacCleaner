# Changelog

All notable changes to MacCleaner will be documented here.

Format: [Keep a Changelog](https://keepachangelog.com/en/1.0.0/)
Versioning: [Semantic Versioning](https://semver.org/spec/v2.0.0.html)

---

## [2.8.0] — 2026-08-14

Three new safe cleanup targets, plus two report-only `doctor` checks that
explain disk and memory pressure MacCleaner deliberately does not act on.

### Added
- Three new `safe` cleanup targets in existing categories (no new categories,
  no changed IDs — purely additive):
  - `xcodebuildmcp-workspaces` (`xcode`) — `~/Library/Developer/XcodeBuildMCP`,
    workspace scratch data written by the XcodeBuildMCP tool. Regenerated on
    the next build. Measured 1.0 GB on the dev machine.
  - `chrome-optimization-model-store` (`caches`) — `~/Library/Application
    Support/Google/Chrome/optimization_guide_model_store`, Chrome's downloaded
    on-device ML prediction models. Re-downloaded on demand.
  - `chrome-optimization-hint-cache` (`caches`) — `~/Library/Application
    Support/Google/Chrome/*/optimization_guide_hint_cache_store`, Chrome's
    per-profile page-optimization hint cache. Globbed across every profile
    (`Default`, `Profile 1`, …) and regenerated on demand.
- `doctor` gained a **`Swap`** check (report-only). Reads `sysctl
  vm.swapusage` and reports used-of-total with a percentage, flagging
  `ok: false` at 75% or more. This check **reclaims nothing and offers no
  cleanup action**: macOS owns the swap file and shrinks it on its own as
  memory pressure drops, so there is nothing here MacCleaner could safely
  delete. It exists to explain disk usage you'd otherwise go hunting for.
  Zero total swap reports "not in use".
- `doctor` gained a **`Held-open files`** check (report-only, and shown only
  when the total reaches 500 MB). Runs `lsof -nPw +c 0 +L1` to find files
  that have been deleted but are still held open by a running process, and
  names the largest holders. **It reports; it does not act** — and
  deliberately so: that space comes back by itself the moment the holding
  process exits, so the fix is to quit or restart that process, not to
  delete anything. MacCleaner will never kill a process on your behalf. The
  sum dedupes by device+inode, so one deleted file held by several processes
  (or several file descriptors) is counted once rather than multiplied.

### Notes
- Both new checks are best-effort. If `sysctl` or `lsof` is missing, exits
  non-zero, times out, or prints something unparseable, the check degrades
  quietly — `Swap` reports "could not determine swap usage" and `Held-open
  files` is omitted entirely. Neither can fail a `doctor` run.
- `doctor` still always exits `0`, including when either new check reports
  `ok: false`. Parse the JSON `ok` field rather than the exit status.
- Both thresholds (75% swap; 500 MB held-open) are hardcoded. No new
  `config.json` keys were added in this release.

## [2.7.0] — 2026-08-13

App Uninstaller: find orphaned app leftovers left behind after you delete an
app the normal way (drag to Trash), without ever guessing.

### Added
- New `leftovers` category: a bundle-ID-precise scanner for cache/preference/
  saved-state files an app left behind under `~/Library` after you removed
  it. Detection is never fuzzy — an entry only surfaces when its directory
  or preference-file name is shaped like a reverse-DNS bundle ID (e.g.
  `com.example.app`) *and* that exact bundle ID has no matching installed
  app under `/Applications`, `~/Applications`, or `/System/Applications`
  (including one level inside a vendor wrapper folder, e.g. Adobe apps, and
  a symlinked `.app` — some vendors and Nix/home-manager-style installs use
  one). Apple's own bundle ID, anything under `com.apple.*` (including its
  `group.com.apple.*` app-group variant, e.g. `group.com.apple.mail`), and
  any candidate that's a strict sub-domain of an installed bundle ID (e.g.
  Squirrel.Mac's `.ShipIt` updater domain under an installed app) are always
  excluded, so MacCleaner can never flag itself, the OS, or an installed
  app's own updater/helper domains. Matching is bundle-ID-precise, not name-
  or fuzzy-based, so an installed app whose `.app` is enumerated correctly
  is never flagged. As a second, more thorough confirmation pass (3rd
  whole-branch review), every remaining candidate is also checked against
  Spotlight — a single batched, case-insensitive `mdfind` query for all
  candidates at once, never one call per candidate — and excluded if
  Spotlight reports a real `.app` bundle anywhere on disk with that exact
  bundle ID, regardless of location, depth, or wrapper-folder nesting. This
  is additive on top of the directory walk, not a replacement for it, and
  degrades to a no-op on any failure (mdfind missing, Spotlight disabled,
  timeout). Confirmed on the real dev machine: Adobe Creative Cloud four
  directories deep, several Adobe helper daemons, a Brother printer
  utility, and a Steam-bundled game — none reachable by the bounded
  directory walk alone — are now correctly recognized as installed. The
  true residual after this pass: helper, updater, and shared/framework
  preference domains that have no `.app` of their own anywhere (e.g. a
  shared vendor settings domain like `com.microsoft.shared`, or a system
  framework domain like `org.cups.printingprefs`); genuinely orphaned data;
  `.app` bundles nested *inside another app's own `Contents/` folder*,
  which Spotlight's application importer does not index as independent
  items even after a forced reindex (confirmed with Alfred 5's internal
  preferences helper); and ordinary Spotlight indexing lag or a per-volume
  index that's disabled. None of these are guessed around — every hit
  stays `safe: false` and review-only rather than auto-cleaned regardless
  of how either signal performs.
- Five locations are scanned, each one Apple already keys by bundle ID:
  `~/Library/Caches`, `~/Library/Preferences`, `~/Library/Saved Application
  State`, `~/Library/HTTPStorages`, and `~/Library/WebKit`.
- New config key `app_leftover_min_age_days` (default `7`) — orphaned data
  younger than this is never offered, so an app you just uninstalled a
  minute ago (or reinstalled under a new name) doesn't show up as an
  immediate false alarm.
- Two new env overrides, `MACCLEANER_INSTALLED_APPS_DIRS` and
  `MACCLEANER_LEFTOVER_LIBRARY_ROOT`, so both the installed-app enumeration
  and the `~/Library` scan root can be sandboxed in tests (and for anyone
  scripting their own dry runs) — same pattern as `MACCLEANER_TMP_ROOT`.
- Like the `tmp` and `simulators` categories, every `leftover-*` target is
  dynamic (`categories --json` never lists one) and always `safe: false` —
  it needs an explicit `--targets leftover-<id> --yes` or interactive
  confirmation; an unattended `clean --yes` never touches it.

### Deliberately out of scope for v1
Three other bundle-ID-adjacent locations are **not** scanned yet:
- **Containers** — still precisely bundle-ID-matched, but a materially
  bigger blast radius per target since sandboxed apps can keep substantial
  persistent data there. A reasonable v1.1 addition once the base scanner
  has field experience.
- **Application Support** — keyed by an app's *display name*, not its
  bundle ID. No reliable identifier survives app deletion, so matching it
  would mean guessing — the exact imprecision bundle-ID matching was chosen
  to avoid.
- **LaunchAgents** — a leftover agent plist is inert on its own, but a
  helper daemon could in theory still be loaded even after its parent app
  is gone; safely handling that needs `launchctl` interaction this release
  doesn't take on.

A future version could snapshot a bundle-ID → display-name map while apps
are still installed (e.g. during `scan`), which would let a later release
match Application Support by name even after the app's gone — real new
statefulness, so it's noted here as a deliberate boundary, not an oversight.

---

## [2.6.1] — 2026-08-12

### Fixed
- `docker system df`'s estimator was reading the wrong table column and silently dropping rows whose TYPE name has two words ("Local Volumes", "Build Cache") — reporting 4.2 GB reclaimable for "Docker unused data" when only 660 MB actually was, and the number barely moved after cleaning since it was really tracking total image size rather than what's stale. `Local Volumes` is now deliberately excluded from the estimate too: the safe `docker-prune` target's command never removes volumes (they commonly hold real data), so counting volume space as reclaimable would advertise bytes that specific safe, one-click target can never free.
- `doctor`'s "Menu bar app" / "Engine/App version" checks hardcoded the literal `/Applications` path with no test-isolation seam, so their tests silently depended on whether the *real* machine happened to have an app installed there. New `MACCLEANER_SYSTEM_APPLICATIONS_DIR` override, matching the existing `MACCLEANER_TMP_ROOT`/`MACCLEANER_LAUNCH_AGENTS_DIR` pattern.

---

## [2.6.0] — 2026-08-12

Glass & Sparkle: a full visual overhaul of the menu bar app, plus self-updating
installs.

### Added
- Complete app redesign — sidebar navigation replaces the tab bar, glass
  panels, monospaced sizes, per-category color dots, and a cyan accent, all
  driven from one design system so light and dark mode stay in sync instead
  of drifting independently
- Rich menu bar popover replaces the plain text menu — a disk-usage ring,
  a reclaimable-space hero number, your top 3 categories, and a one-click
  "Clean safe items" action with a self-expiring "freed" confirmation
- Select All / None on the Projects tab, and All / None / Safe only on the
  Dashboard — closes the bulk-selection gap reported after the v2.5 UI
- Live per-item progress during cleans — items show a spinner while running
  and settle into their real per-item result, instead of the UI freezing
  until the whole run finishes
- Sparkle auto-updates for installed apps: a daily background check with an
  in-app prompt showing the release notes, plus "Check for Updates…" in
  Settings. Releases are signed with an EdDSA key and publish a
  `releases/latest/download/appcast.xml` feed; see `docs/RELEASING.md`
  "Sparkle appcast" for the mechanics. Homebrew-cask users keep using
  `brew upgrade` as before — Sparkle is additive, not a replacement

### Changed
- Dynamic scanners (`tmp`, `simulators`) are now skipped when a `--targets`
  or `--category` selection can't possibly include them — a targeted clean
  no longer pays the `simctl` + `/private/tmp` walk latency for scanners it
  was never going to use

### Fixed
- The app's History tab (and the menu bar's background refresh) tolerates a
  malformed history entry instead of failing to decode the whole run list
- `clean --targets` with an explicitly empty, whitespace-only, or garbage
  value (`""`, `" , "`, `",,,"`) is now correctly a no-op, not a full safe
  auto-clean. The gate that decides "was `--targets` given at all" now keys
  off argument *presence* (`is not None`) rather than raw-string truthiness
  — a truthiness check treats `--targets ""` the same as the flag being
  absent entirely, which fell through to "no `--targets` given" and
  auto-cleaned everything

### Notes
- This is the first release with Sparkle wired up. The signing key,
  appcast generation, and framework-signing order were built and verified
  locally against a throwaway keypair (see `docs/RELEASING.md`), but the
  live self-update path — an installed 2.6.0 app discovering and applying
  a real signed update — can only be proven end-to-end starting with the
  *next* tagged release
- 22 categories, unchanged from v2.5.0

---

## [2.5.0] — 2026-08-09

AI-era cleanup: two new dynamic scanners for the mess coding agents and
simulators leave behind, plus a bundle-install fix and a category migration
so existing installs pick up new categories automatically.

### Added
- `/private/tmp` build-artifact scanner (`tmp` category) — finds stale Xcode-style DerivedData layouts and stale repo clones with build directories directly under `/private/tmp`, gated by a minimum age (`tmp_min_age_days`, default 3 days) and classified by directory *contents*, never by name. Review-only, and the one narrow, regression-tested carve-out to the home-only delete rule (direct children of the tmp root only, marker-gated — never the root itself, never anything nested, symlinks and out-of-home escapes refused)
- Simulator cleanup (`simulators` category) — stale devices not booted in `simulator_stale_days` (default 30) and runtime images with no devices left, both driven entirely through `xcrun simctl` rather than raw filesystem deletes; every device/runtime identifier from `simctl`'s own JSON is regex-validated before it's allowed anywhere near a shell command
- `codex-sessions` / `codex-archived-sessions` review targets (`ai` category) for OpenAI Codex CLI conversation history under `~/.codex/`. `.gemini` and `.cursor` were deliberately left out — both are browser-profile-shaped (saved logins, installed extensions), not simple cache/log dirs safe for a generic cleanup pass
- Category auto-enable migration (`known_categories`) — categories introduced by a new release now show up automatically for existing installs on upgrade, while a fresh install's deliberate category disables still survive a config reload

### Fixed
- `CONFIG_PATH` is now bundle-aware, with the same Application Support fallback as the other state files — a prerequisite for a future signed/notarized Homebrew cask install; an existing sibling `config.json` beside a non-writable script directory (e.g. a shared/admin-owned `/opt/mac-cleaner` install) is now still read instead of being silently abandoned for a fresh per-user default
- App icon now renders deterministically on alert panels; `install.sh` relaunches a running app after installing so users aren't left running a stale binary
- The `tmp` scanner now honors `skip_paths`, matching every other cleanup target
- `scan --category`/`clean --category` with a valid category that's simply empty right now (the `tmp`/`simulators` scanners often are) exits 0 with well-formed JSON instead of exit 1 — exit 1 stays reserved for an unknown category name
- Tightened the simulator-runtime-identifier validation regex to require the real `com.apple.CoreSimulator.SimRuntime.` prefix, closing a gap where `simctl`-shaped strings like `all`/`--outdated`/`--unusable` could otherwise reach the delete command

### Notes
- 22 categories total (up from 20); `AGENTS.md` and shell completions updated for both new categories and the dynamic-target semantics of `tmp-*`/`simulator-*` IDs
- Homebrew tap publishing follows this release, once notarization is live (see `docs/RELEASING.md` §4)

---

## [2.4.0] — 2026-08-08

The first release since 2.0.0. Versions 2.1–2.3 were developed and merged but
never individually published, so this release rolls all of that work — engine
(2.1), scheduling & notifications (2.2), app experience (2.3), and
distribution (2.4) — into one.

### Added
- 17 new cleanup targets across 3 new categories — `flutter` (Dart pub cache), `php` (Composer), `vms` (Colima, Vagrant, minikube) — plus yarn classic cache, npm logs, conda clean, sccache, LM Studio & Whisper models, Xcode DocumentationCache, Cypress, MS Teams, Zoom updater, Terraform plugins, and Expo caches
- Disk snapshots: every scan/clean records free space + reclaimable to `snapshots.log` (365 daily-deduped entries, roughly a year of history); `report` shows a disk trend and `report --json` gains `disk_history`
- Git-aware `projects`: dirty or unpushed repos are badged and excluded from `--yes` sweeps (config `project_git_check`)
- `clean --dry-run` / `projects --dry-run`: exact resolved paths + sizes, zero side effects
- launchd scheduling replaces cron — a clean whose scheduled time passed while the Mac was asleep now runs on wake instead of being skipped. An existing cron schedule is removed automatically the next time you run `scheduler.sh weekly` or `scheduler.sh monthly` (not a bare `scheduler.sh` invocation, which never touches the crontab)
- `schedule` subcommand (`status`/`weekly`/`monthly`/`off`, `--json`) — launchd scheduling is first-class engine logic; `scheduler.sh` is a thin wrapper that `exec`s into it, so every existing invocation (`weekly`/`monthly`/`remove`/`status`) keeps working with the same exit codes. New `MACCLEANER_LAUNCH_AGENTS_DIR` env override
- Notifications when a scheduled clean finishes (`clean --notify`, used by the launchd agent), and in-app notifications after a clean
- `disk-check` — a cheap hourly low-disk watch installed alongside any schedule; warns below `low_disk_threshold_gb` (default 10 GB), throttled to at most one warning per day
- Live menu bar — free disk and "last cleaned" refresh every minute; the full reclaimable scan runs on a long interval (`full_refresh_hours`, default 6) plus on wake and when the menu opens
- In-app schedule management — Settings gained a Schedule section (Off / Weekly / Monthly), so turning scheduling on or off no longer requires the terminal. `doctor`'s Schedule check now shares the same state helper as `schedule status`
- Settings toggles for notifications, low-disk alerts, and the threshold
- Dashboard disk trend chart — a Swift Charts view plotting free space per day from `report --json`'s disk history, with the low-disk threshold drawn as a rule line
- App icon
- Shell completions for zsh and bash — subcommands, per-subcommand flags, config keys, and live category/target-ID completion from the engine (cached, with a timeout and static fallback). Installed automatically by `install.sh` and shipped in the CLI tarball
- Release-time code signing and notarization, gated on repository secrets: the workflow ships ad-hoc signed exactly as before when they are absent, and signs, notarizes, and staples automatically once they exist — no workflow change needed
- `Casks/maccleaner.rb` — a validated Homebrew cask, plus `docs/RELEASING.md` documenting the signing secrets, the release steps, and how to publish the tap

### Changed
- `doctor`'s Schedule check reports launchd agents, and flags a legacy cron entry
- Shell shortcuts (`maccleaner`, `mclean`, `mpreview`, `mreport`) are now functions instead of aliases — zsh's `complete_aliases` is off by default, so aliases never reached the completion system. `install.sh` migrates existing alias lines in place

### Notes
- The public Homebrew tap is intentionally unpublished until releases are notarized: Homebrew 6 removed `--no-quarantine`, so an unsigned cask cannot launch cleanly and there is no supported workaround

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

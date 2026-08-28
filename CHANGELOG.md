# Changelog

All notable changes to MacCleaner will be documented here.

Format: [Keep a Changelog](https://keepachangelog.com/en/1.0.0/)
Versioning: [Semantic Versioning](https://semver.org/spec/v2.0.0.html)

---

## [2.13.0] — 2026-08-28

The "largest items" view could only see a small slice of a real disk, and one
of the numbers it did show was off by a factor of a hundred.

### Added
- **Applications and the whole home Library are now covered.** Default roots go from three to six: `~/Documents`, `~/Downloads`, `~/Desktop`, **`~/Library`**, **`~/Applications`**, **`/Applications`**. On the machine this was built against, `~/Library` alone is 85 GB against ~35 GB of Documents — the old three roots simply could not see where the disk had gone. `storage-insights --json` now also echoes `roots` and `min_bytes`, so "nothing large in Documents" is distinguishable from "Documents was never scanned". Result cap raised 50 → 100 to fit the wider scope.
- **Bundle-aware scanning.** A `.app`, `.framework`, `.photoslibrary`, `.xcarchive` (and similar — see `STORAGE_INSIGHTS_BUNDLE_SUFFIXES`) is reported as **one entry carrying its whole size**, flagged `is_bundle: true`, and never descended into. This is what makes `/Applications` work at all: it holds **zero** loose files over the 100 MB floor and a dozen multi-GB app bundles, so a file-only scan showed nothing there. Listing an app's internal binaries would also be actively misleading, since deleting one breaks the app. The app's list shows a distinct icon for a bundle so an 11 GB row reads as "this is a whole application".

### Fixed
- **Sparse files reported ~100× too large.** Sizes came from `st_size` (apparent size). Docker's `Docker.raw` reports **1.0 TB apparent against 9.97 GB actually allocated**, so a phantom terabyte sat at the top of a largest-items list on a 460 GB disk — obvious nonsense that discredits every other number on the page. Sizes now use `st_blocks * 512`, matching `du` and Finder, and are also correct (smaller) for APFS-compressed files: Xcode drops from a claimed 8.8 GB to a true 4.0 GB.
- **Storage tab layout was broken.** Each row's proportional bar used a `GeometryReader`, which is greedy in both axes — it claimed the entire row width, shoving the category, size and action buttons hard against the window edge and stretching the view past its pane, which pushed the header and breadcrumb off the top of the window entirely. The bar now draws on a fixed-width track needing no measurement, and the view's bounds are pinned explicitly. The bar fraction is also clamped, so a level reporting 0 bytes can no longer produce a `NaN` width.

### Notes
- Two pre-existing tests were assertion-shaped rather than intent-shaped and broke on purely additive changes: one pinned the exact key set of a scan result (so adding `is_bundle` failed a test about the *delete pipeline*), and one pinned the exact default-root list. Both now assert the invariant they actually care about. A third scanned the developer's **real home directory** in-process — it passed at 33 s and later blew a 300 s timeout on unchanged code, purely because the machine had filled up; it now redirects `HOME` to a temp dir and runs in 0.2 s.

---

## [2.12.0] — 2026-08-28

An accuracy release. Every item here came from a real investigation on a
working developer Mac where MacCleaner reported 190 MB reclaimable against a
disk sitting at 94% full — three separate reasons, all of them the tool's
fault rather than the machine's.

### Fixed
- **"Total reclaimable" was overstated.** It was a plain sum over targets, but 27 targets nest inside `general-caches` (the review-level sweep of all of `~/Library/Caches`), so every one of their bytes was counted twice — **2.5 GB of overstatement** on a real machine, and more on a fuller one. `reclaimable_total()` now reports the union: a target whose every path lies inside another target's path contributes nothing extra, because nobody can free the same byte twice. Per-target sizes are unchanged and still individually accurate; only the aggregate moved. Nesting uses a trailing-separator check, so `/x/Caches2` is correctly *not* treated as a child of `/x/Caches`.
- **`get_size()` now passes `du -x`.** Without it, `du` descends into any mounted volume beneath the path and counts that disk image's contents *on top of* the image file — the same bytes twice. Measured that way by hand, one directory read **106 GB against an actual 11 GB**, a threefold error that misdirected an entire storage investigation. No current target path contains a mount point, but nothing checks that when a target is added, so it now measures correctly by default.

### Added
- **`System temp` — a third advisory `doctor` check.** macOS's per-user temp directory (`$TMPDIR`) had accumulated **20 GB across ~15,000 orphaned entries** — more than every cleanable target combined — and nothing surfaced it. Flags at 5 GiB **or** 2000 entries, because size alone badly under-reports the problem: the same directory later measured 16,289 entries holding 3 GB, having been 20 GB hours earlier. The entry count is the durable signal that tools are leaking scratch; the byte figure just depends on when you look. Report-only, exactly like `Swap`: it names the remedy (restart — macOS clears `$TMPDIR` at boot), never becomes a target, never deletes, and never affects top-level `ok`. `/tmp` is deliberately excluded, since that is the cleanable `tmp` scanner's territory.

### Changed
- `run_doctor()` returns `{"ok", "checks"}` instead of a bare bool. A function that computed fifteen checks and discarded fourteen of them made individual checks impossible to assert on without re-parsing printed JSON. `main()` still discards the value, so **`doctor` still always exits 0** — the CI smoke test's contract is unchanged.

### Notes
- The `doctor` pressure-check tests mocked two of the three collectors and left the third live, so every assertion in that class silently depended on the size of the running machine's `$TMPDIR`. They passed only because that directory happened to be small. All three are now mocked.

---

## [2.11.1] — 2026-08-27

### Added
- **`xcode-derived-data-custom`** (`xcode`, safe) — `~/Library/Developer/*DerivedData*`. The existing `xcode-derived-data` target only knows Xcode's default location, so a project pointed at a custom DerivedData path (Xcode → Settings → Locations, or `-derivedDataPath`) was invisible to every scan. On the machine this was found on, MacCleaner reported **190 MB reclaimable while 6.2 GB of pure build output** sat in `~/Library/Developer/RecovrDerivedData*`. The glob matches direct children of `~/Library/Developer` only, so it cannot reach the default location one level deeper inside `Xcode/` — no double-counting, pinned by a test that checks with `glob` rather than `fnmatch` (whose `*` spans `/` and would have hidden the overlap). Static targets: 93 → 94.

### Notes
- Two storage sinks found during the same investigation are **outside MacCleaner's reach by design** and worth knowing about: `/private/var/folders/<…>/T` (macOS's per-user temp directory) had accumulated **20 GB across ~15,000 entries**, mostly orphaned `TemporaryDirectory.*`, simulator device temp dirs and Interface Builder scratch; macOS clears this on restart, which is the safe way to reclaim it. Separately, `/private/tmp` held **8.4 GB** of AI-coding-session working directories whose nested `DerivedData`/`node_modules` are pure build output — the `tmp` scanner correctly refuses these while they are under `tmp_min_age_days` (3) old, since a live session may still be using them.

---

## [2.11.0] — 2026-08-27

MacCleaner could tell you about 10 GB of rebuildable caches while your disk sat
at 92% full, and had no way to show you the other 390 GB. This release adds the
missing half: a read-only browser over the whole disk, including everywhere the
cleanup engine is deliberately forbidden to go.

### Added
- **`storage-map` — a read-only whole-disk browser.** `maccleaner storage-map [PATH]` lists one level of children beneath a path (default `$HOME`), largest first, with sizes and a display category (`applications`, `documents`, `media`, `developer`, `caches`, `appdata`, `system`, `other`). Unlike every other command in the engine it **reads outside `$HOME`** — `/Applications`, `/Library`, `/` all work — because a cache cleaner scoped to your home folder structurally cannot answer "where did my disk go". `--min-size MB` hides small entries; `--json` for the machine contract.
- **Storage tab in the app** (`⌘2`). Drill into any folder, jump straight to `/`, `/Applications`, `/Library`, `~/Library` or `~/Documents`, see a proportional bar per row, Reveal in Finder, and **Move to Trash**.
- **"Show in Dock" setting.** MacCleaner has always been an `LSUIElement` app — menu bar only, no Dock icon, not addable to the Dock. That is now a preference (Settings → Appearance, config key `show_in_dock`, default off so nothing changes for existing users). The bundle still *launches* as an accessory so there's no Dock flash while settings load; `CleanerBridge` promotes it to a regular app at runtime when the setting is on, which is why settings now load from the bridge's initialiser rather than a view's `.task` — a menu-bar-only session may never open a window at all.

### Fixed
- **The unused-simulator-runtime target could never fire on current Xcode.** The scanner validated a runtime's `identifier` against a pattern matching only the `com.apple.CoreSimulator.SimRuntime.*` form. Xcode 26's `simctl runtime list -j` puts an image **UUID** in that field and moves the reverse-DNS string to `runtimeIdentifier`, so every runtime failed validation and was silently discarded — indistinguishable from "nothing to clean", while 8 GB of genuinely unused runtime sat there. The check now accepts both shapes, and both remain strictly shell-safe (the validation exists because these strings are interpolated into a `simctl` command). The test fixture only modelled the old shape, which is why this was never caught; a fixture matching real Xcode 26 output has been added alongside it.

### Notes
- **Measurement must not cross mount points.** `storage-map` runs `du -xkd 1`. Without `-x`, `du` descends into mounted disk images and counts their contents *on top of* the image files themselves — the same bytes twice. Measured that way by hand, `/Library/Developer/CoreSimulator` on a real machine reported **106 GB** against an actual **11 GB**, a threefold overstatement that sent an entire storage investigation down the wrong path before it was caught. `TestStorageMap.test_does_not_cross_mount_points` pins the flag. The older `get_size()` helper (`du -sk`, no `-x`) has the same latent flaw and is currently harmless only because every target path it measures is inside `$HOME` and free of mount points — worth fixing before that stops being true.
- **Deletion from the storage browser is Trash-only, and deliberately not the cleanup engine's delete path.** That path is built for rebuildable caches and hard-deletes; anything reachable from the browser may be irreplaceable personal work. `FileManager.trashItem` is recoverable and lets macOS's own permission rules decide what is off limits — a system file simply fails, which is the correct answer.

---

## [2.10.0] — 2026-08-23

Ten new cleanup targets, taking the static table from 83 to 93. Each was found
by scanning a real working developer Mac and verified to exist with real size
before being added — none were guessed from vendor documentation.

### Added
- **`caches`** — `chrome-http-cache` (Chrome's per-profile HTTP and compiled-code cache under `~/Library/Caches/Google/Chrome`; verified to hold no cookies, history, or logins), `spotify-browser-cache`, `clang-module-cache` (clang/SourceKit precompiled modules), and `electron-updater-pending` (a glob, `~/Library/Caches/*electron-updater/pending`, for update installers Electron apps have downloaded and staged).
- **`ai`** — `ollama-updates` and `codex-sparkle-updates` (both staged app updates that simply re-download), plus two **review-only** targets: `codex-runtimes` (a running Codex session may be executing out of that directory, so removing it can break work in flight, not merely delay the next start) and `antigravity-browser-profile` (a full Chromium profile with live logins — deleting it signs you out).
- **`node`** — `typescript-cache`, the TypeScript language server's automatic `@types` acquisition cache.
- **`rust`** — `rustup-downloads`, partial or interrupted toolchain downloads. The installed toolchains under `~/.rustup/toolchains` are never touched.
- **Multi-path targets.** `get_targets()`'s `add()` gained a `paths=[...]` form for a target whose regenerable content sits in several sibling directories whose shared parent must not be swept. `_target_paths()` already understood this shape; `skip_paths` is applied per entry, so excluding one path retires just that path rather than the whole target.

### Fixed
- **Caught in review before shipping:** the Spotify target was originally a single `safe=True` path at `~/Library/Caches/com.spotify.client`. That directory looks like a cache root but is actually the Spotify desktop app's embedded-Chromium profile — `Default/Login Data`, `Default/Cookies`, `Browser/Cookies`, `Local State`, and the `WidevineCdm` DRM module sit there beside the real caches, so an unattended `clean --yes` would have silently destroyed live session state with no confirmation. It now names only the two regenerable subdirectories (`Browser/Cache` and `Data`), recovering ~83% of the space with none of the risk. Offline-downloaded music was never in scope either way — it lives under Application Support and is untouched. Two new regression tests generalise the lesson beyond this one target: `test_no_target_points_at_a_chromium_profile_root` fails if **any** `safe=True` target resolves to a directory containing a `Login Data`, `Cookies`, or `Local State` file, and `test_labels_are_unique` fails on duplicate labels (the original name, "Spotify cache", collided exactly with the existing `spotify-cache`, rendering two indistinguishable rows in the TUI and app).

### Notes
- `completions/complete_data.py` — the committed snapshot of the engine's target list that the completion test harness runs against — has no generator script, so it silently drifts when targets are added. `CLAUDE.md` now documents the exact regeneration command, including why it must read `DEFAULT_CONFIG` rather than `load_config()` (the latter would bake the regenerating developer's own `skip_paths` exclusions into the committed file).
- Six of the ten new targets nest under `~/Library/Caches`, which the review-level `general-caches` target also sweeps. `scan` does not de-duplicate overlapping targets, so "total reclaimable" now double-counts roughly 4.5 GiB more than before on a machine like the test one. This is long-standing behaviour (`electron-cache`, `pip-cache` and others already nest the same way) and is not a regression, but the magnitude is larger now and it is worth addressing separately.

---

## [2.9.1] — 2026-08-16

### Fixed
- **Low-disk alert now shows the real MacCleaner icon when the app is running.** Every automatic notification (`disk-check`'s hourly low-disk warning, `clean --notify`'s scheduled-clean banner) is posted from a background helper process via `osascript "display notification"`, which has no way to attribute a notification to an app or give it a custom icon — macOS always shows a generic one. The SwiftUI app now performs its own low-disk check on its existing periodic tick and, when it's running, delivers the alert itself through the same in-process mechanism already used for the "cleanup finished" banner (which has always shown the real icon). `disk-check` gained a `--no-post` mode and a new `should_notify` JSON field so the app can ask "is an alert due right now" without the CLI's own `osascript` post firing — and both share the existing 24h throttle in `alerts.json`, so the app and the standalone launchd `diskwatch` agent never double-notify for the same dip. When the app isn't running, the launchd agent's own alert still fires exactly as before, with the generic icon, as the reliable fallback.
- An earlier version of this fix tried `terminal-notifier` (an optional Homebrew tool with a `-sender` flag that can post as another app's identity) instead. It was reverted after testing showed it hangs indefinitely — not merely fails — when triggered the same way MacCleaner's own alerts are actually triggered: from a background launchd job with no active foreground session. Confirmed with a real, temporary LaunchAgent invoking it directly, not just from a script. No code from that approach shipped.
- Also fixed: a pure menu-bar-only session (the Dashboard window never opened) never started the periodic 60-second refresh that free-space and low-disk monitoring depend on — only a one-off refresh each time the menu bar icon was clicked. The menu bar popover now also starts that recurring refresh the first time it's opened, which is effectively guaranteed for anyone actually using the app.

---

## [2.9.0] — 2026-08-14

### Added
- **`storage-insights` subcommand** — a read-only scan for individually large files sitting in the three places they tend to accumulate unnoticed: `~/Documents`, `~/Downloads`, and `~/Desktop` (overridable via `MACCLEANER_STORAGE_INSIGHTS_ROOTS`, colon-separated). Reports files at or above a **100 MB floor**, largest first, capped at the **top 50** results. Both numbers are hardcoded constants (`STORAGE_INSIGHTS_MIN_BYTES`, `STORAGE_INSIGHTS_MAX_RESULTS`) — no config key or flag changes them. `--json` returns `{"version", "entries": [{"path", "size_bytes", "size_human", "mtime"}, ...]}`; without it, a table renders via `rich` (or a plain-text fallback).
- **Architecturally separate from the delete pipeline, by design.** `storage-insights` is not a target, belongs to no category, and is never touched by `clean`, `clean --yes`, or `--dry-run` — it only reports, it never offers to delete anything. This is a deliberate scope boundary: large personal files (videos, disk images, exports) are exactly the kind of thing MacCleaner's safe-delete model is wrong for, since "large" doesn't mean "safe to remove" the way a rebuildable cache does.
- **iCloud-eviction-safety guarantee.** The scan is stat-only: it walks each root's tree with `os.scandir`/`os.stat` and never opens, reads, or downloads file contents. This matters specifically for iCloud Drive–backed folders, where opening an evicted (cloud-only) file triggers a download that can hang or stall the scan — `storage-insights` reports the file's on-disk size from metadata alone and never triggers that download. Verified end-to-end and timed against a real, non-fixture `~/Documents`/`~/Downloads`/`~/Desktop` — completes in a few seconds with no hang.
- **Dashboard "Large Files" section** (app) — a new panel above the existing target groups, populated from `storage-insights --json`, listing each file's size and a **Reveal in Finder** action (`NSWorkspace.shared.selectFile(_:inFileViewerRootedAtPath:)`). Purely informational — the app never offers to delete a large file from this section, matching the CLI's read-only design. Distinguishes a genuine "no large files found" result from a scan failure: an older installed engine (`~/mac-cleaner/cleaner.py`, out of date because `install.sh` hasn't been re-run) that predates this subcommand surfaces a specific "Requires engine 2.9.0+" message via a dedicated `storageInsightsError` state, rather than being misreported as an empty result (see `AGENTS.md` engine/app version-skew notes).

---

## [2.8.1] — 2026-08-14

### Fixed
- `_safe_to_delete()` — the single guard every delete across every category routes through — checked path containment lexically (`Path.absolute()`), which never resolves symlinks. A target reaching its destination through a symlinked ANCESTOR directory (e.g. a glob match under a symlinked cache subdirectory) was lexically inside `$HOME` but could physically resolve outside it, and would have been deleted. Fixed by resolving the parent directory (not the leaf) before the containment check. The leaf is deliberately left unresolved: `_remove()` unlinks a symlink leaf rather than following it, so a symlink whose own directory entry is inside `$HOME` stays safe to remove even when it points elsewhere — only the link is ever removed, never its target. Also closes a narrower, currently-unreachable gap where a literal trailing `..` path component could resolve outside `$HOME` even with the parent fix in place. No current target — static, glob, or any dynamic scanner — is known to have produced a path that triggered this; found and fixed proactively via adversarial review of the core delete-safety invariant, not from a reported incident.

---

## [2.8.0] — 2026-08-14

Three new cleanup targets, plus two report-only `doctor` checks that account
for disk space MacCleaner deliberately refuses to touch.

**Neither new check reclaims a single byte.** They exist to explain storage
you'd otherwise go hunting for, and the tool deliberately declines to act on
either condition — so both are marked `advisory` and are excluded from
`doctor`'s top-level `ok`.

### Added
- Three new cleanup targets in existing categories (no new categories, no
  changed IDs — purely additive; the static target table goes to 83):
  - `xcodebuildmcp-workspaces` (`xcode`, **safe**) —
    `~/Library/Developer/XcodeBuildMCP`, workspace scratch data written by the
    XcodeBuildMCP tool. Regenerated on the next build. Measured 1.0 GB on the
    dev machine.
  - `chrome-optimization-hint-cache` (`caches`, **safe**) — `~/Library/
    Application Support/Google/Chrome/*/optimization_guide_hint_cache_store`,
    Chrome's per-profile page-optimization hint cache. Globbed across every
    profile (`Default`, `Profile 1`, …) and regenerated on demand.
  - `chrome-optimization-model-store` (`caches`, **review-only**) —
    `~/Library/Application Support/Google/Chrome/
    optimization_guide_model_store`, Chrome's downloaded on-device ML
    prediction models. Marked `safe: false`, so `--yes` never touches it and
    it must be selected deliberately: Chrome indexes these models in its
    `Local State` file, which MacCleaner does not touch, so "Chrome just
    re-downloads them" is an assumption we could not verify — and we won't
    auto-delete on an unverified recovery path.
- `doctor` gained a **`Swap`** check (report-only, advisory, always present).
  Reads `sysctl vm.swapusage` and reports **how much disk the swapfiles
  consume** — `total` there is exactly the size of the swapfiles macOS has
  materialised under `/System/Volumes/VM`, which is the only swap number a
  disk tool has any business reporting. Flags `ok: false` at **8 GiB or more
  of swapfiles on disk**. The used/total *percentage* is reported as
  informational text only and triggers nothing: it is not monotonic in disk
  consumed (a healthy machine sits at 90–94% for weeks; a laptop that swapped
  800 MB exactly once reads 78%), so it is useless as a threshold. **This
  check reclaims nothing and offers no cleanup action** — macOS owns the
  swapfiles and grows and reclaims them on its own. There is no "restart to
  free it" advice, because that would be misleading: the swapfiles come back
  within minutes. Zero total reports "no swapfiles on disk".
- `doctor` gained a **`Held-open files`** check (report-only, advisory, and
  shown only when the total reaches 500 MB). Runs `lsof -b -nPw +c 0 +L1` to
  find files that have been deleted but are still held open by a running
  process, and names the largest holders. **It reports; it does not act** —
  and deliberately so: that space returns only once *every* process holding
  the inode exits, and MacCleaner will never kill a process on your behalf.
  The sum dedupes by device+inode, so one deleted file held by several
  processes (or several file descriptors) is counted once rather than
  multiplied; when the total spans more than one mounted volume the status
  says so, since `doctor`'s `Disk` row covers the startup volume only.
- **`advisory` key on `doctor` JSON checks.** A `checks[]` entry may now
  carry `"advisory": true`. It marks a report-only observation MacCleaner
  refuses to act on and offers no remedy for. The key is emitted **only when
  true**, so every pre-existing check entry is byte-identical — purely
  additive, per the JSON contract. **Advisory checks are excluded from the
  top-level `ok` aggregate**: an advisory check reporting `ok: false` does
  not flip `ok`. That keeps `ok` meaning "there is a MacCleaner-owned problem
  with a fix" rather than "something is unhealthy but nothing can be done".
  Both new 2.8.0 checks are advisory in every branch; no pre-2.8.0 check is.

### Notes
- Both new checks are best-effort. If `sysctl` or `lsof` is missing, exits
  non-zero, times out, or prints something unparseable, the check degrades
  quietly — `Swap` reports "could not determine swap usage" and `Held-open
  files` is omitted entirely. Neither can fail a `doctor` run, and neither
  parser can raise out of it.
- `doctor` still always exits `0`, including when either new check reports
  `ok: false`. Parse the JSON `ok` field rather than the exit status.
- All thresholds (8 GiB of swapfiles; 500 MB held-open total, 10 MB
  per-process naming floor) are hardcoded. No new `config.json` keys were
  added in this release.

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

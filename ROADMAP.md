# MacCleaner Roadmap

This document tracks planned features and long-term direction. Community input welcome — open an issue or PR to discuss anything here.

---

## Current State — v2.18.1 ✅

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
- **v2.16 additions — "nothing should require faith"** — the schedule had been reported broken while working perfectly, because it was invisible. `schedule run` kickstarts the **real** launchd agent, so a success is evidence that Monday's run will work rather than a simulation, and `schedule status --json` gained `next_run` (ISO, `null` when off) derived from the same calendar rules the plists encode. `clean --min-free GB` cleans safe targets largest-first and **stops the moment free space reaches the threshold** — built for agents under a fail-closed disk floor, with `target_met` reported honestly, so exhausting every safe target without reaching the floor is `false` rather than a quiet success. `storage-map --depth N` (1–3) returns nested `children` for one-call subtree audits, depth-1 output staying byte-identical to the existing contract. The app's Settings gained next clean, last clean, and a Run Now button
- **v2.17 additions — the V3 soak, and what it caught** — the app now bundles `mck` and, after each full scan, runs it read-only against the same config, compares both engines' target tables, and appends any disagreement to `soak.log` (`v3_soak`, default on). Python remains the engine of record; the soak's only job is to earn V3 its trust before Stage 4 ever ports deletion. **It caught a real bug on its first live run**: 763 targets from each engine, exactly one divergence — `general-caches` at Python 0 B against Swift 6.86 GB. The Swift number was the right one. `du` exits non-zero when any subdirectory is unreadable while still printing a correct total for everything it could read, and `get_size()` gated on that exit code and discarded the total, so `~/Library/Caches` measured **0 bytes** against a real 6.4 GB and silently understated every scan. Two more fixes came straight out of running it for real: notarization rejected 2.17.0 because the newly embedded `mck` was unsigned (now signed with hardened runtime before the outer app), and the first live soak **hung**, because stat on a cloud-evicted entry under `~/Library/Caches` blocks indefinitely — `Scanner.duBytes` now carries the same 120s hard cap the Python engine has. 2.17.2 finished that thread on the Python side, after two engines were found wedged 20+ minutes in `getdirentries64` on an evicted iCloud folder: the old "stat-only, so it can never trigger a download" guarantee held for files but not for *listing* a dataless directory. Directories are now tested for `SF_DATALESS` before `scandir` and counted rather than entered, the walk carries a time budget, and the app gained both a hard subprocess timeout and a re-entrancy guard. Also in 2.17.2: `doctor` lists every installed copy of the app and fails when there are two, naming the Homebrew-managed one, and the hourly `disk-check` trims `cron.log` **in place** (launchd holds it open `O_APPEND`, so a rename-rotate would strand that descriptor on the old inode)
- **v2.17.3** — the bound is the fix: `storage-insights` now runs its walk in a daemon thread joined against the time budget, because a directory can block `readdir` forever without being iCloud-dataless (an Xcode app container hung a real scan for 12 minutes; it now returns in 120 s with `truncated: true`); `projects` skips evicted iCloud folders before descending; the app's Storage tab, misrendered since 2.11 (header gone, sidebar apparently empty), was root-caused to one `fixedSize` caption inflating the section's minimum height to 1513 pt, and a CI layout probe now measures every section; the Homebrew cask drops the deprecated `verified:` parameter
- **v2.18 additions** — complete read-only Swift command estimates; readable and correctly ordered Storage results with honest partial measurements; bounded discovery/probes; serialized state and configuration updates; preserved settings on failed sync transitions; corrected preview, selection, legacy parsing, sizing, and notifications. 618 Python tests plus Swift/parity/app probes. Six additional safety changes were held for owner review and subsequently approved for v2.18.1.

- **v2.18.1 safety fixes** — honest deletion accounting, excluded-descendant protection, stable dynamic IDs, bounded discovery, empty-only symlink refusal, and supervised cleanup process groups. 651 Python tests plus Swift/parity/app probes. Rescan after upgrading to obtain updated lossy dynamic IDs.

---

## Next — v2.x Ideas

### Direction after 2.18.1 — reviewed 2026-10-19

The previous buildable backlog shipped in 2.18.0–2.18.1 ([run report #98](https://github.com/Fullex26/MacCleaner/issues/98)); the remaining old checkboxes were Swift deletion/cutover and two entries for the same App Store initiative. The next cycle should make the existing developer cleaner easier to trust, inspect, and operate while collecting real evidence for V3.

Three directions were considered: expanding cleanup coverage, finishing the Swift migration, and improving the current cleanup workflow. **Prioritize the current workflow and soak evidence:** coverage needs measured developer-machine examples, and deletion migration cannot bypass calendar-time soak. Avoid adding features solely to lengthen this document.

**Status rules:** “Ready to specify” means there is a concrete incident or inspected capability gap, not that a design or implementation is approved. Start each item with its stated fixture/probe and keep one item per PR; a source observation is not a claimed reproduced runtime bug. Discovery items below are hypotheses, outside the implementation queue until their validation produces evidence. Sizes are relative: **S** one focused change, **M** a feature across engine/bridge/UI, **L** several separately reviewed changes; they are not delivery promises. Version assignments follow validation rather than dates.

**Recommended first batch:** R01 → R02 → R03, then R04/R05 and R09/R10. These improve safety communication and produce the evidence needed to choose the next larger investment. All JSON additions remain optional/additive; Python stays stdlib-only and the app remains a thin engine client.

#### Priority 1 — Make cleanup decisions and outcomes explicit

- [ ] **R01 · Show unavailable scan measurements — Implemented, pending release · S.** Evidence: #78 added `measurement_error` with `exists: false`, but `ScanTarget` in [CleanerBridge.swift](app/Sources/CleanerBridge.swift) does not decode it and [DashboardView.swift](app/Sources/DashboardView.swift) filters absent targets out; users can miss why an expected cache disappeared. Add an unavailable state and bounded retry, first proving the current behavior with the real bridge; done when a failed measurement stays visible, cannot be selected as a known-size candidate, and a successful retry clears the warning without breaking older JSON.

- [ ] **R02 · Inspect exact cleanup scope before confirming — Implemented, pending release · M.** Evidence: `clean --dry-run --json` already exposes resolved paths/commands ([AGENTS.md](AGENTS.md)), while the Dashboard confirmation operates on selected IDs; #73 and #80 demonstrate why parent scope and symlinks matter. Add an on-demand preview for the frozen selection using the existing engine contract; done when path and command targets are clearly distinguished, preview errors are visible, preview has zero writes, and execution revalidates current safety rules rather than trusting stale preview paths.

- [ ] **R03 · Explain per-item cleanup outcomes in History — Ready to specify · M.** Evidence: #66 preserves deletion errors and #90 separates notification successes/failures, but [HistoryView.swift](app/Sources/HistoryView.swift) renders date, bytes, and a non-skipped count without item details. Add expandable outcome rows and separate deleted/trashed/skipped/failed counts; done when an error-only and a partial-success fixture remain understandable after relaunch and historical records without optional details still decode.

- [ ] **R04 · Manage protected paths in Settings — Ready to specify · M.** Evidence: #71/#73 implemented exclusion safety, and `skip_paths` exists in engine config, but [SettingsView.swift](app/Sources/SettingsView.swift) offers no editor. Add folder selection, a readable protected-path list, and explicit removal through existing config transactions; done when exclusions survive concurrent setting updates and sync, and the UI explains that protecting a descendant can withhold a whole parent target.

- [ ] **R05 · Configure project scan scope in the app — Ready to specify · M.** Evidence: `project_roots` and `project_min_age_days` exist in [cleaner.py](cleaner.py) and CLI flags, while Settings does not expose them. Add root and age controls backed by config commands; done when invalid/unavailable roots are explained, saved settings affect both CLI and app scans, and dirty/unpushed project protection stays enabled and visible.

- [ ] **R06 · Preserve successful-command warnings for app users — Ready to specify · M.** Evidence: #69/#78 emit partial-discovery warnings on stderr, while `runEngine` returns only stdout on exit zero; the CLI user can see information the app client cannot. Specify a bounded diagnostics result at the bridge or an additive engine warning envelope, keeping stdout valid JSON; done when a successful but incomplete fixture shows a warning, ordinary success remains quiet, and warning text is never interpreted as permission to clean.

#### Priority 2 — Explain storage and support problems

- [ ] **R07 · Surface doctor results in the app — Ready to specify · M.** Evidence: `doctor --json` already distinguishes actionable installation/config faults from advisory swap, held-open files, and Docker image observations ([AGENTS.md](AGENTS.md)); the current sidebar has no health view. Add an on-demand diagnostic view keyed by check name with optional advisory decoding; done when advisories never become cleanup actions or a failed overall health gate, and unknown future checks render safely.

- [ ] **R08 · Create a local, reviewable support export — Ready to specify · M.** Evidence: #61 required layout evidence, and reports #94/#98 assembled versions, probes, and release facts manually. Export a user-selected local bundle of versions, doctor results, diagnostic summaries, and optional logs with a preview/redaction step; done when usernames, home paths, project names, and user-supplied config values are excluded or explicitly approved, output is size-bounded, and nothing is uploaded automatically.

- [ ] **R09 · Report soak eligibility and failure reasons — Ready to specify · S.** Evidence: `runSoakIfEnabled` silently skips missing `mck` or a missing `~/mac-cleaner/config.json`, while Settings shows only the last divergence count. Expose disabled, unavailable, running, failed, and compared states with the actual config source; done when missing-helper and bundled-engine fixtures cannot be mistaken for “engines agree,” and resolving alternate layouts never compares different configurations.

- [ ] **R10 · Build an auditable soak summary — Ready to specify · M.** Evidence: [V3's gate](docs/V3-SWIFT-ENGINE.md) requires clean real-use evidence across a release cycle, but the UI reports only the last comparison and `soak.log` is a line log. Add local records of attempted/completed/skipped comparisons, engine versions, timestamps, coverage and divergence classes plus a summary/export; done when zero comparisons cannot pass readiness, incomplete/malformed logs are reported, and fixtures are explicitly excluded from real-machine evidence.

- [ ] **R11 · Measure command-target parity during real soak — Ready to specify · M.** Evidence: #64 added full command-estimate parity in fixture CI, but `soakCompare` still bypasses existence/size comparison for `sw.cmd`. Design a read-only, timing-aware comparison using the new estimates; done when deliberate command divergence is recorded, unavailable tools are distinguished from agreement, and documented tolerance handles timing drift without hiding safety or target-presence mismatches.

- [ ] **R12 · Automate published-release verification — Ready to specify · M.** Evidence: #98 required separate manual checks for ZIP/CLI contents, Ed25519, stapling, Gatekeeper, stable appcast, and two casks; [RELEASING.md](docs/RELEASING.md) also contains historical setup prose that no longer describes current publication. Turn those checks into a repeatable read-only verifier and refresh the current procedure; done when a wrong hash, missing appcast, stale engine, or channel-version mismatch fails clearly, and the verifier never publishes or rewrites a release.

#### Priority 3 — Make quality evidence repeatable

- [ ] **R13 · Validate the rendered app on real Macs — Ready to specify · M.** Evidence: #61 measured a 1513 pt layout minimum against a 480 pt window, and #98 explicitly stops short of visual acceptance. Create a repeatable fixture-backed visual/interaction checklist for every section, narrow/default windows, long labels, empty/loading/error/partial states, and light/dark appearance; done when dated actual renders and keyboard navigation results accompany the existing minimum-size probe, with real data hidden and visual failures reproduced before fixes.

- [ ] **R14 · Extend real-bridge contract coverage — Ready to specify · M.** Evidence: #83/#86 addressed incomplete map results, #87 stale navigation results, and #90 outcome notifications through focused probes; optional fields can still be dropped by individual models as R01 illustrates. Inventory every Codable envelope against current, legacy, empty, partial, and error fixtures; done when omissions with user impact have named regression cases, unknown additive keys decode, and the test exercises production bridge types rather than copies.

### Discovery backlog — validate before implementation

These ten investigations deliberately expand the choices without claiming ten new bugs or committing to ten features. Each must yield a measurement, reproducible fixture, or observed user need before promotion into the queue above. A negative result closes the investigation; it does not justify speculative product code.

- [ ] **D01 · Accurate selected-space estimates · Hypothesis · S.** Evidence anchor: the engine already unions nested targets, while Dashboard selected/pending totals sum row sizes. Seed a parent plus nested child and compare the displayed promise with the engine's union; promote only if the result misleads, then specify a selection estimate that also labels command estimates and Trash behavior honestly.

- [ ] **D02 · Storage growth attribution · Hypothesis · M.** Evidence anchor: daily disk snapshots and `report --stats` exist, but they answer free-space trend and cleanup value, not which directory grew. Measure whether repeated user-requested maps explain real low-disk incidents; a prototype must preserve scan scope/completeness, avoid counting changing coverage as growth, and establish retention/IO costs before proposing stored per-path history.

- [ ] **D03 · Search and filter large result sets · Hypothesis · S.** Evidence anchor: 94 static targets plus dynamic projects/leftovers can produce long lists, and the recorded first soak had 763 targets. Exercise representative fixture sizes and keyboard tasks; add search, category, risk, or age filters only where measured navigation is poor, with hidden selections explicitly accounted for in confirmation.

- [ ] **D04 · User cancellation for read-only scans · Hypothesis · M.** Evidence anchor: #69/#78 bound walks at 120 seconds and the app has a 600-second backstop. Measure abandoned navigation and slow-scan wait times; first prove a cancel operation can reap owned subprocesses, suppress stale results, and leave state consistent, without extending the design to abandoned background deletion.

- [ ] **D05 · Scan latency and resource budget · Measurement study · M.** Evidence anchor: the former 12-minute hang and subsequent hard bounds prove liveness, not acceptable normal latency. Record phase durations, subprocess counts, CPU and memory on real small/large developer machines; propose optimizations only for measured bottlenecks, with cold/warm runs and incomplete results reported separately.

- [ ] **D06 · Target expansion from measured developer storage · On-device verification required · M.** Evidence anchor: prior targets reclaimed measured developer space, but the Spotify profile incident showed cache-looking roots can contain credentials and live state. Collect opt-in path, allocated-size, owning-tool, rebuild-cost and liveness evidence for uncovered storage, check overlaps with all current targets, and add no target or safe flag until its exact regenerable boundary is verified on a real Mac.

- [ ] **D07 · Explain why a candidate is withheld · Hypothesis · M.** Evidence anchor: exclusions, tmp liveness, age gates, incomplete app inventory, and project git guards intentionally suppress candidates. Collect support cases where that absence is confusing; prototype a bounded, on-demand explanation that identifies the applicable guard without bypassing it, exposing private path inventories by default, or making normal scans noisier.

- [ ] **D08 · Accessibility and reduced-motion audit · Verification study · M.** Evidence anchor: the app uses custom glass panels, proportional bars, icon actions, and category colors, while the existing layout probe measures geometry. Inspect VoiceOver labels/order, keyboard-only use, increased contrast and reduced motion on actual macOS; promote only observed failures into focused fixes, preserving explicit text for safety and outcome states.

- [ ] **D09 · Recovery expectations for Trash cleanup · Product investigation · M.** Evidence anchor: recoverable mode exists, but command-based targets are still external cleanup commands and Trash does not immediately free disk space. Test users' interpretation of preview, confirmation and minimum-free results; prefer accurate scope/help first, and consider an undo ledger only after separately designing destination collisions, restart persistence, and explicit ownership without promising command rollback.

- [ ] **D10 · Scheduled-clean observability after Run Now · Verification study · S.** Evidence anchor: Settings' existing comment records a functioning schedule being reported broken because it was invisible; `schedule run` proves launchctl accepted a start, not that cleanup completed. Trace controlled accepted-start, delayed, failed and completed runs; if the current UI conflates those states, specify persisted last-attempt/outcome reporting without adding a second scheduler or changing doctor exit semantics.

### Gated milestones — evidence and decisions still required

- **V3 gate A: evidence collection.** R09–R11 make evidence understandable; they do not satisfy the gate themselves. Run the released read-only dual-engine comparison on real developer machines for at least one release cycle, record versions/coverage, and investigate divergences. Missing runs, synthetic fixtures, or repeated CI runs do not count as soak.
- **V3 gate B: deletion design and review.** Only after gate A and existing guard parity pass, specify the caller port with the Python safety corpus, failure accounting, exclusions, symlink behavior and process shutdown preserved. Deletion code and safe-flag changes still require explicit owner review; no Swift deletion API is authorized by this roadmap expansion.
- **V3 gate C: controlled cutover.** After reviewed deletion parity, plan the documented Python fallback release and observable engine selection; retain compatibility tests and define rollback evidence before changing the default.
- **App Store decision.** Treat the two old MAS checkboxes below as one initiative: decide whether a separate read-only storage product is worth maintaining, then validate actual Swift API coverage and sandbox folder-access behavior before scoping packaging. App Store Connect setup/submission remains owner-authorized work; it is not assumed to be “just packaging.”

### Deliberate exclusions

No guessed cache paths, automatic cleanup of swap/system temp/held-open files/Docker VM images, cloud telemetry service, account system, or automatic upload of support data. Duplicate-file deletion, broad “optimize my Mac” actions, and a second commercial SKU are not implied by developer storage cleanup. Reconsider only with concrete user need and a separate safety/product design.

### Previously shipped v2.x work

- [x] **Do not embed failed Swift build output** — a hermetic build left an executable but exited unsuccessfully, and the fallback still embedded it. Require successful native compilation before querying or copying its result; part of the build-output fix. Shipped in v2.18.0.

- [x] **Resolve Swift build output through SwiftPM** — the real macOS build succeeded under `.build/out/Products/Release` but the app script looked under `.build/apple/Products/Release` and omitted the soak engine. Query `--show-bin-path` for the successful build configuration instead of assuming a layout. Shipped in v2.18.0.

- [x] **Report deletion failures accurately (#66)** — a mocked removal failure was silently credited as reclaimed space. Preserve errors and account only successful removal; shipped in v2.18.1.
- [x] **Protect excluded descendants (#73)** — isolated fixtures showed a parent cleanup target could include a configured excluded child. Apply overlap checks across static and dynamic targets; shipped in v2.18.1.
- [x] **Keep lossy dynamic IDs stable (#76)** — singleton-to-different-singleton fixtures reused old normalized IDs even without a simultaneous collision. Bind lossy project/log/tmp/leftover names to stable identity digests; shipped in v2.18.1.
- [x] **Bound remaining read-only walks (#78)** — blocked enumeration, globbing, empty-only pre-enumeration, and Caskroom lookup fixtures exceeded deadlines. Bound discovery itself and fail closed on incomplete inventory or measurement; shipped in v2.18.1.
- [x] **Reject empty-only symlink roots (#80)** — a cache-root symlink to home exposed personal-file children to cleanup and preview. Refuse the symlink before enumeration; shipped in v2.18.1.
- [x] **Stop cleanup groups on interruption (#88)** — timeout, immediate app shutdown, and interruption during process launch each left harmless fixture descendants writing after failure. Coordinate engine/app shutdown and defer launch-time signals until supervision exists; shipped in v2.18.1.
- [x] **Rebuild the committed fallback app (v2.18.0)** — its tracked engine and metadata remained at 2.17.2 despite newer releases. Rebuilt on macOS from the release sources.


- [x] **Preserve configuration on failed sync transitions** — injected write/link failures during sync-off and both sync-on branches remove the active config, silently changing Trash mode back to permanent deletion. Commit the local binding atomically only after its replacement is ready. Shipped in v2.18.0.
- [x] **Count cleanup outcomes accurately in app notifications** — an exact-bridge probe with an error-only response produced “1 items cleaned”. Count deleted/trashed items as successes and report failures separately. Shipped in v2.18.0.

- [x] **Honor standalone legacy category selection** — a parser-only probe shows documented `--category node` exits 2 while `--preview --category node` works. Translate the standalone spelling into a scan and pin repeatable/category-value variants. Shipped in v2.18.0.

- [x] **Preserve failed Storage-map measurements** — injecting a du timeout into an existing temporary folder returned a zero total without the incomplete flag, bypassing the app warning. Propagate failed or partial measurement status through map results and nested levels. Shipped in v2.18.0.

- [x] **Clean up Swift probe descendants after shell exit** — an isolated compiled probe returned output while its background sleep remained alive. Terminate the read-only process group on every exit path, including successful shell completion. Shipped in v2.18.0.
- [x] **Keep the newest Storage navigation result** — an isolated real-bridge harness completed `/fast` before an earlier `/slow`, then the older result replaced the new destination. Track request generations so stale completions cannot replace the map, error, or busy state. Shipped in v2.18.0.
- [x] **Terminate estimate descendants on timeout** — an isolated real-helper probe returned zero after its shell timed out while a PID-recording child remained alive. Supervise read-only estimate commands in their own process group and terminate that group at the deadline or shell completion. Shipped in v2.18.0.

- [x] **Show incomplete Storage maps as unavailable** — the bounded enumeration fallback in #78 returns `incomplete: true`, but the Swift bridge discards it and displays a nonempty folder as zero bytes. Decode the additive flag and show a partial/unavailable state instead of the empty-folder message. Shipped in v2.18.0.
- [x] **Deduplicate hard links inside bundle sizes** — a temporary app containing an 8 KiB file and a second hard link reports 16 KiB in storage insights while allocated blocks and `du` report 8 KiB. Count each device/inode once within a bundle traversal. Shipped in v2.18.0.

- [x] **Measure sparse Storage-map files by allocated blocks (v2.18.0)** — A temporary 1 GiB sparse file on this Mac occupied zero allocated bytes but appeared as a 1 GiB child under a zero-byte map total. Use allocated blocks for loose-file rows and size filtering, matching directory measurements and storage insights.
- [x] **Serialize config sync transitions with setting writes (v2.18.0)** — A deterministic temporary-file reproduction showed sync-off restoring `delete_mode: rm` after a concurrent setter successfully selected Trash mode. Lock the local binding as well as shared destinations so sync-on/off cannot race preference writes.


> Incremental improvements on the v2.0 foundation. A few of these pull forward items from the phases below.

- [x] **Require a persisted app alert claim** — two no-post checks both authorized delivery when a simulated alert-state write failed. Withhold app delivery when the throttle claim cannot be saved, while reporting any notification already posted accurately. Shipped in v2.18.0.

- [x] **Disambiguate project artifact IDs** — `foo-bar/node_modules` and `foo_bar/node_modules` produce the same slug; an explicit ID selects both and bypasses both git protections. Use stable path digests for colliding slugs and reject ambiguous legacy IDs. Shipped in v2.18.0.
- [x] **Honor project exclusions** — review reproduced `scan_projects` returning an artifact beneath a configured `skip_paths` prefix, contrary to the never-touch contract. Apply exclusions during discovery, including root aliases and artifact parents containing excluded descendants. Shipped in v2.18.0 (#71).
- [x] **Serialize configuration changes** — two stale config copies applying separate settings reverted `delete_mode` from Trash to permanent deletion. Reload and mutate under one transaction lock for each setting/category update. Shipped in v2.18.0.
- [x] **Align minimum-free previews** — an explicit review target appears in `--min-free --dry-run` even though the real minimum-free clean excludes it. Share the safe-only selection policy and preserve dry-run side-effect guarantees. Non-finite thresholds also reach integer conversion unchecked; reject invalid thresholds before discovery. Shipped in v2.18.0, with eligible candidates explicitly identified as an upper bound.
- [x] **Bound all Swift read-only subprocesses** — a controlled `ps` probe waited 11.3 seconds, exceeding Python's 10-second cap; `ps`, `simctl`, and Spotlight helpers have unbounded reads. Reuse the estimate runner's process-group deadline and file-backed output for these probes. The existing `du` deadline passed a descendant-output probe and is unchanged. Shipped in v2.18.0.

- [x] **Bound project discovery itself** — a blocked `scandir` reproduction held `scan_projects` until the filesystem operation returned despite a requested short budget. Run root validation and the recursive discovery walk in a daemon with a deadline, retain completed candidates, and warn when results are partial. Shipped in v2.18.0.
- [x] **Serialize state transactions** — isolated competing processes lost one of two report entries and snapshot days, and both claimed the same low-disk notification. Protect each read/modify/write transaction with a bounded interprocess lock while retaining atomic JSON replacement. Shipped in v2.18.0.

- [x] **Empty project selection must select nothing** — reproduced with a temporary project: `projects --targets "" --yes --dry-run` offered one artifact rather than zero, and the same truthiness gate controls real cleanup. Treat selector presence separately from its parsed IDs, matching the regular clean command. Shipped in v2.18.0 (#67).
- [x] **Readable Storage bars** — Jordan's screenshot shows tiny bars when `~/Library` dominates the home map; `StorageView` divides each row by the entire root total. Shipped in v2.18.0: scale against the largest sibling, retain clamping, and pin the behavior in the app probe.

- [x] **V3 command-target estimates** — shipped in v2.18.0: strict stub-tool parity reproduced 15 field divergences, including four zero estimates and discarded simulator sizes. The read-only kit now carries generated estimate metadata, bounded execution, and full command-target size/existence parity.

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

- [ ] **Full Swift rewrite of cleaner engine** — **stages 1–3 landed, plus Stage 4's first half**: contract fixtures (`tests/fixtures/`, regenerated-and-diffed by the suite) and the read-only `MacCleanerKit` (`swift/`, generated target table, CI-enforced Swift/Python parity via `tools/check_swift_parity.py`). Stage 3's dual-engine soak now runs in the app and has already caught a real bug in each engine, and Stage 4's **guards** are ported — `Guards.safeToDelete` (parent resolved, leaf deliberately unresolved, exact 2.8.1 semantics) and `Guards.tmpScanPathAllowed` (the two-level carve-out) — with `tools/check_guard_parity.py` judging a 20-scenario adversarial corpus against BOTH engines in CI, so no deletion caller can be written without verdict parity existing first. What remains is Stage 4's deletion callers, gated on accumulated soak evidence rather than on engineering effort, then Stage 5 cutover, per `docs/V3-SWIFT-ENGINE.md`. Deliberately not attempted as one rewrite: the Python engine is the tested core, and parity for a deletion tool must be proved, not assumed
- [x] **SwiftUI preferences window** — Settings tab in the v2.0 app, persisted through the same `config.json` as the CLI
- [ ] **Sandboxed App Store build** — assessed in `docs/APP-STORE-FEASIBILITY.md`: the sandbox denies exactly what MacCleaner does, so a MAS variant would be the read-only storage X-ray (`storage-map`/`storage-insights`), not the cleaner. The read-only Swift core it would ship now exists (V3 stages 1–3), but the read-only variant still needs a product decision, an API-coverage audit, and validated sandbox folder access before packaging
- [x] **Sparkle auto-updater** — shipped in v2.6.0, see Phase 4
- [x] **Universal binary** — `app/build.sh` compiles arm64 + x86_64 and lipos them together

---

## Phase 6 — App Store & Beyond

> Long-term, if the project gains traction.

- [ ] **Mac App Store release** — see `docs/APP-STORE-FEASIBILITY.md`; V3 stage 3 has landed, so the Swift read-only core exists and the MAS variant still needs verified API coverage, sandbox access design, and a product decision before packaging. Still requires owner-level App Store Connect actions
- [x] **iCloud sync for config** — `config sync on|off|status` (v2.15.0): config.json lives in iCloud Drive behind a symlink, shared by the CLI, the app, and the launchd agents; `off` is local-only and leaves the iCloud copy for other Macs
- [x] **Usage analytics (opt-in)** — `report --stats` (v2.15.0), implemented local-first: aggregates this machine's own `report.log` by target and category to answer "which categories are most valuable" with nothing ever leaving the machine. A networked telemetry backend was deliberately not built
- [x] **Scheduled scan reports** — the weekly scheduled clean's completion notification now carries a trailing-7-day digest (v2.15.0) — the weekly run's notification is the weekly report, with no second agent and no plist change. Email was deliberately not built (no send infrastructure; notifications are the native channel)

---

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) to get started. The easiest first contributions are adding new cleanup targets in `cleaner.py` — each one is a small dictionary entry in `get_targets()` with a stable `id`.

New cleanup target ideas are tracked in [issues](../../issues) with the `cleanup-target` label.

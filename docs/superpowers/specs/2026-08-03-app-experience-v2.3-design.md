# App Experience v2.3 — In-App Schedule Management + Disk Trend Chart

**Date:** 2026-08-03
**Status:** Approved
**Scope:** `cleaner.py`, `scheduler.sh` (becomes a wrapper), `app/Sources/`, tests, docs. No distribution work (that spec is queued separately).

Sub-project 3 of the v2.x program, reprioritized ahead of Distribution because the maintainer uses the GUI ~95% of the time. The two features close the largest GUI gaps: the schedule is currently terminal-only, and the snapshot data recorded since v2.1 is invisible in the app.

---

## Goals

1. **`schedule` subcommand** — scheduling becomes engine logic (`schedule status|weekly|monthly|off`, all `--json`), so the app manages it through the same JSON contract as everything else, and app-only (cask) installs can schedule without `scheduler.sh`.
2. **Settings: Schedule section** — Off / Weekly (Mon 9am) / Monthly (1st 9am) radio group with a live status line.
3. **Dashboard: disk trend chart** — free-space-over-time from `disk_history.snapshots`, with the low-disk threshold as a rule line.

## Non-goals

- Custom schedule days/times (presets only — matches the CLI, no new plist surface).
- Reclaimable-over-time as a second chart series (nullable data makes it gappy; revisit on demand).
- Any Distribution work (queued spec), Sparkle, or new notification behavior.
- A Swift test target (still the documented gap; Swift changes are verified by compilation plus the maintainer's eyes).

## Guiding constraint

Unchanged from v2.0: the engine is the brain. The app gains **no** plist, launchctl, or cron logic — it calls `schedule …` and renders JSON. All JSON changes are additive.

---

## 1. Engine — `schedule` subcommand

Port `scheduler.sh`'s current logic into `cleaner.py`, preserving behavior exactly:

- Same agent labels (`com.fullex.maccleaner.clean`, `com.fullex.maccleaner.diskwatch`), same plist shapes (`StartCalendarInterval` Weekday 1 / Day 1 at 09:00; `StartInterval` 3600), same `EnvironmentVariables` PATH, same `StandardOutPath`/`StandardErrorPath` (`cron.log` beside the engine).
- Same launchctl chain: `bootout` → `bootstrap gui/$UID`, falling back to `unload`/`load`; real stderr surfaced on failure; a failed load means the command reports failure (exit 1) and never claims success — but the plist is still written so manual loading works.
- Same cron migration semantics: `weekly`/`monthly` detect a legacy line via the anchored `mac-cleaner/cleaner.py` marker, echo it, strip it (sparing unrelated lines), and report the detected cadence (the explicitly requested cadence wins); `off` also strips a legacy line; `status` is read-only.
- `MACCLEANER_LAUNCH_AGENTS_DIR` env override honored (tests sandbox with it plus stub `launchctl`/`crontab` on PATH, as today).

**Commands and JSON (all additive):**

- `schedule status --json` → `{"version", "schedule": "weekly"|"monthly"|null, "agents": [{"label", "plist_present": bool, "loaded": bool}], "legacy_cron": bool}`. The `schedule` value is derived from the clean agent's plist trigger (`Weekday` ⇒ weekly, `Day` ⇒ monthly, absent ⇒ null). Exit 0 always.
- `schedule weekly|monthly --json` → installs/replaces both agents, migrates legacy cron, then emits the same shape as `status` plus `"migrated_cron": bool`. Exit 0 on success, 1 when a load failed.
- `schedule off --json` → unloads and removes both agents, strips a legacy cron line, same status shape plus `"removed": bool`. Exit 0 (removing an absent schedule is success, not failure).

**Interpreter and engine paths in the plist:** `sys.executable` and `Path(__file__).resolve()` — this is what makes scheduling work when the only engine on the machine is the one inside `MacCleaner.app/Contents/Resources/` (cask/app-only installs). That path is stable across app updates.

**`scheduler.sh` becomes a thin wrapper**: `weekly|monthly` → `python3 "$SCRIPT_DIR/cleaner.py" schedule weekly|monthly`; `remove` → `schedule off`; `status` → `schedule status`; usage text updated to say so. Exit codes pass through. Every existing invocation keeps working; the human output stays equivalent (the engine prints the same ✅/⚠️ style messages in non-JSON mode).

**`doctor`** reuses the new status helpers instead of its own glob + `launchctl` code — one source of truth.

## 2. App — Settings Schedule section

New `Section` in `SettingsView` (above Notifications):

- Radio group (same `.radioGroup` picker style as delete mode): **Off** / **Weekly — Mondays 9am** / **Monthly — 1st at 9am**.
- Backed by two bridge additions following the existing patterns: `loadSchedule()` runs `schedule status --json` (decoded into a new optional `ScheduleStatus` model) and `setSchedule(_ choice:)` runs `schedule weekly|monthly|off` then re-reads status. Failure → `statusMessage`, and the picker reverts to the last known real state (not the optimistic one).
- Status line under the picker: "Active — runs Mondays at 9am · low-disk check hourly", "Not scheduled", or — when `legacy_cron` is true — "A legacy cron schedule exists; choosing an option migrates it."
- The section renders only when `schedule status` succeeds; if the engine is too old to know the subcommand (exit 2), the section shows "Update the CLI to manage scheduling here" rather than erroring — the superset rule in UI form.

## 3. App — Dashboard disk trend chart

- `HistoryReport`'s `DiskHistory` model gains `snapshots: [DiskSnapshot]?` (additive, optional): `ts: String`, `disk_free_bytes: Int`, `disk_total_bytes: Int` (ignore the nullable reclaimable fields).
- New `DiskTrendView` using **Swift Charts** (system framework, macOS 13+ = the app's existing floor): `LineMark` of free GB per day (x: day from `ts` via the bridge's existing `parseTimestamp`, y: GB), plus a `RuleMark` at `low_disk_threshold_gb` labeled "Low-disk warning". Y-axis clamps to include 0 and the max of (peak free, threshold ×1.2).
- Placement: top of the Dashboard tab, ~140pt tall, above the category list.
- Empty state (<2 snapshots): the chart area shows "Disk trends appear after a couple of days of scans" in secondary text — no blank rectangle.
- Data flows from the existing 60s light tick (`report --json` already returns the full snapshot list); no new engine calls.

## 4. Version, docs, tests

- `VERSION = "2.3.0"`; `app/Info.plist` 2.3.0; committed `MacCleaner.app` bundle rebuilt (mandatory whenever `app/Sources/` changes).
- `AGENTS.md`: full `schedule` contract (commands, JSON shapes, exit codes, env override). `CLAUDE.md`, `README.md` (Settings can now manage the schedule; Dashboard shows trends), `ROADMAP.md`, `CHANGELOG.md` (`## [2.3.0] — Unreleased`).
- **Tests** (stdlib, sandboxed exactly as `TestScheduler` today — stub `launchctl`/`crontab` on PATH, tempdir `MACCLEANER_LAUNCH_AGENTS_DIR`, never the real machine):
  1. `schedule status --json` shape on empty, installed-weekly, installed-monthly, plist-present-but-not-loaded, and legacy-cron states.
  2. `schedule weekly|monthly` install both agents, plists lint (`plutil -lint`), reinstall replaces, `migrated_cron` true only when a line was stripped, unrelated cron lines survive, explicit cadence wins over detected.
  3. `schedule off` removes both, strips legacy cron, exits 0 when nothing installed.
  4. Load-failure path: stub `launchctl` failing → exit 1, real stderr surfaced, no success claim, plist still written.
  5. `scheduler.sh` wrapper: each command delegates and passes exit codes through (the existing `TestScheduler` CLI tests largely become these).
  6. `doctor` Schedule check consistency with `schedule status` (same underlying helpers).
- Swift: compilation via `app/build.sh` with zero new warnings; chart and Settings section verified by the maintainer's eyes (listed as the release's manual checks).

import Foundation
import AppKit
import Combine

// ── JSON models (contract with cleaner.py --json, see AGENTS.md) ───────────────

struct ScanTarget: Codable, Identifiable, Hashable {
    let id: String
    let category: String
    let label: String
    let description: String?
    let size_bytes: Int
    let size_human: String
    let safe: Bool
    let exists: Bool?
}

struct DiskStats: Codable {
    let total_bytes: Int
    let free_bytes: Int
    let used_bytes: Int
    let percent_used: Double
}

struct ScanReport: Codable {
    let timestamp: String
    let disk: String
    let disk_stats: DiskStats?
    let total_reclaimable_bytes: Int
    let total_reclaimable_human: String
    let targets: [ScanTarget]
}

struct CleanItem: Codable, Identifiable {
    let id: String
    let label: String
    let freed: Int
    let status: String
    let error: String?
}

struct CleanResult: Codable {
    let delete_mode: String?
    let freed_bytes: Int
    let freed_human: String
    let disk_after: String?
    let items: [CleanItem]
}

/// `null` when git status couldn't be determined (not a repo, `git` missing,
/// any git failure, or `project_git_check` disabled); otherwise the two
/// signals `projects --json` reports per AGENTS.md §"projects --json".
struct GitInfo: Codable, Hashable {
    let dirty: Bool
    let unpushed: Bool
}

struct ProjectArtifact: Codable, Identifiable, Hashable {
    let id: String
    let path: String
    let kind: String
    let project: String
    let age_days: Int
    let size_bytes: Int
    let git: GitInfo?
}

struct ProjectsReport: Codable {
    let roots: [String]
    let min_age_days: Int
    let total_bytes: Int
    let artifacts: [ProjectArtifact]
}

struct HistoryRun: Codable, Identifiable {
    let timestamp: String
    let total_freed_bytes: Int
    let total_freed_human: String
    let disk_after: String
    let items: [CleanItem]
    var id: String { timestamp }

    private enum CodingKeys: String, CodingKey {
        case timestamp, total_freed_bytes, total_freed_human, disk_after, items
    }

    // FIX7 idiom (see DiskSnapshot's comment just below): report.log is a
    // mutable, externally-editable file — one entry missing or misshaping
    // `items` (hand edit, partial write, a future schema change) must not
    // fail decoding of the whole HistoryReport. That used to freeze both
    // the History tab (this struct's own consumer) and
    // performLightRefresh()'s 60s menu bar tick, whose `try?` swallows the
    // decode error and whose `guard let … else { return }` then bails
    // silently, leaving the free-space/"Last cleaned" display stuck. Every
    // other field here was already required pre-FIX7 (and a run with a
    // missing timestamp/freed total is arguably not recoverable as a
    // display row anyway) — only `items` gets the tolerant treatment,
    // matching the reviewer's ask precisely: default to `[]` rather than
    // losing the whole run over a missing/malformed items array.
    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        timestamp = try container.decode(String.self, forKey: .timestamp)
        total_freed_bytes = try container.decode(Int.self, forKey: .total_freed_bytes)
        total_freed_human = try container.decode(String.self, forKey: .total_freed_human)
        disk_after = try container.decode(String.self, forKey: .disk_after)
        items = (try? container.decodeIfPresent([CleanItem].self, forKey: .items)) ?? []
    }
}

struct DiskCurrent: Codable {
    let free_bytes: Int
    let total_bytes: Int
}

struct DiskSnapshot: Codable, Identifiable {
    // Optional: the engine's `load_snapshots` only filters out non-dict
    // entries, so one externally-corrupted entry (e.g. missing a key) must
    // not fail decoding of the whole `HistoryReport` — that used to freeze
    // the menu bar's free-space and "Last cleaned" display, since
    // `performLightRefresh`'s `guard … else { return }` bails out silently
    // on any decode failure. The chart skips entries it can't use instead.
    let ts: String?
    let disk_free_bytes: Int?
    let disk_total_bytes: Int?
    let id = UUID()

    private enum CodingKeys: String, CodingKey {
        case ts, disk_free_bytes, disk_total_bytes
    }
}

struct DiskHistory: Codable {
    let current: DiskCurrent
    let snapshots: [DiskSnapshot]?
}

struct HistoryReport: Codable {
    let runs: [HistoryRun]
    let disk_history: DiskHistory?
}

struct CategoryInfo: Codable, Identifiable {
    let name: String
    let description: String
    let enabled: Bool
    var id: String { name }
}

struct CategoriesReport: Codable {
    let categories: [CategoryInfo]
}

struct AgentStatus: Codable, Identifiable {
    let label: String
    let plist_present: Bool
    let loaded: Bool
    var id: String { label }
}

struct ScheduleStatus: Codable {
    let schedule: String?
    let agents: [AgentStatus]
    let legacy_cron: Bool
}

struct EngineConfig: Codable {
    var delete_mode: String?
    var notifications: Bool?
    var low_disk_alerts: Bool?
    var low_disk_threshold_gb: Double?
    var full_refresh_hours: Double?
}

enum BridgeError: LocalizedError {
    case engine(String)
    var errorDescription: String? {
        if case .engine(let msg) = self { return msg }
        return nil
    }
}

/// Thread-safe byte accumulator for draining a pipe from its readability handler.
final class PipeBuffer: @unchecked Sendable {
    private var data = Data()
    private let lock = NSLock()

    func append(_ chunk: Data) {
        lock.lock()
        data.append(chunk)
        lock.unlock()
    }

    var contents: Data {
        lock.lock()
        defer { lock.unlock() }
        return data
    }
}

// ── Bridge: all cleaning logic lives in cleaner.py; this only runs it ──────────

@MainActor
final class CleanerBridge: ObservableObject {
    @Published var report: ScanReport?
    @Published var projects: ProjectsReport?
    @Published var history: [HistoryRun] = []
    @Published var categories: [CategoryInfo] = []
    @Published var deleteMode: String = "rm"
    @Published var notificationsEnabled = true
    @Published var lowDiskAlertsEnabled = true
    @Published var lowDiskThresholdGB: Double = 10
    @Published var scheduleStatus: ScheduleStatus?
    @Published var scheduleSupported = true
    /// Set for the whole `setSchedule` round trip (bootout + bootstrap +
    /// `launchctl list`), which is slow enough that the picker would
    /// otherwise visibly snap back to the old value until it completes.
    @Published var isSchedulingBusy = false
    /// The choice the picker should display while `isSchedulingBusy` — the
    /// real `scheduleStatus.schedule` doesn't update until `loadSchedule()`
    /// returns at the end of the round trip.
    @Published var pendingSchedule: String?
    @Published var fullRefreshHours: Double = 6 {
        didSet {
            // Reschedule the periodic timer when the configured cadence actually
            // changes (initial config load, or a later edit) — but only once
            // auto-refresh has actually started (fullTimer != nil), and never on
            // a settings reload that leaves the value unchanged.
            guard fullRefreshHours != oldValue, fullTimer != nil else { return }
            scheduleFullTimer()
        }
    }
    /// Split from a single shared `isBusy` (finding "Also fix"): Dashboard's
    /// `scan()` and Projects' `scanProjects()` are independent subprocess
    /// calls, so a Projects re-scan must not make the Dashboard's own Scan
    /// button/spinner claim to be busy, and vice versa. `fullRefreshIfStale()`
    /// still watches both — see its guard below.
    @Published var isScanning = false
    @Published var isScanningProjects = false
    @Published var isCleaning = false
    @Published var statusMessage: String?
    @Published var lastClean: CleanResult?
    @Published var lastCleanedAt: Date?
    /// Set in the catch block of `clean(ids:)`/`autoCleanSafe()`/
    /// `cleanProjects(ids:)` (finding B2) alongside clearing `lastClean`, so
    /// a failed clean can never render as if the previous run's success was
    /// still current. `statusMessage` alone can't carry this: both
    /// `clean(ids:)` and `autoCleanSafe()` call `scan()` right after the
    /// catch block, and a successful `scan()` sets `statusMessage = nil`,
    /// erasing the "Clean failed" text before the UI ever gets to show it.
    /// Cleared at the start of every clean attempt (success or another
    /// failure both overwrite it correctly) and rendered only while "fresh"
    /// (via `lastCleanFailedAt`), mirroring how `lastClean`/`lastCleanedAt`
    /// already work.
    @Published var lastCleanFailed: String?
    @Published var lastCleanFailedAt: Date?
    @Published var freeBytes: Int?
    @Published var diskSnapshots: [DiskSnapshot] = []
    /// Live per-item clean progress, for Dashboard row spinners/checks (and
    /// any other consumer, e.g. the menu bar popover). The engine only
    /// returns per-item results once the whole `clean` process exits — there
    /// is no true streaming — so `cleaningIDs` is the optimistic "about to
    /// touch these ids" set set by `clean(ids:)` before launching, and
    /// `cleanedIDs` is the real, reconciled-from-`CleanResult.items` set set
    /// after it exits. An id is never added to `cleanedIDs` before the
    /// process actually exits — no "done" state is ever guessed. Both are
    /// cleared the next time `scan()` completes successfully, so a stale
    /// clean's spinners/checks can't leak into a later scan's target list.
    @Published var cleaningIDs: Set<String> = []
    @Published var cleanedIDs: Set<String> = []

    private var lightTimer: Timer?
    private var fullTimer: Timer?
    private var lastFullScan: Date?
    private var wakeObserver: NSObjectProtocol?
    /// Memoized subscription to `UpdaterManager.shared.pendingUpdateVersion`
    /// (finding B1) — set up once (see `observeUpdater()`) regardless of
    /// whether the main window is ever opened, matching `ensureSettingsLoaded()`'s
    /// "menu-bar-only session still works" precedent. Harmless no-op wiring
    /// in the `SPARKLE_DISABLED` stub build, since `pendingUpdateVersion`
    /// there is always nil and the publisher never fires.
    private var updaterCancellable: AnyCancellable?
    /// Memoized so settings load exactly once per app run no matter how many
    /// call sites race to trigger it (menu bar `.task`, main window `.task`,
    /// a notification-gated action) — awaiting it elsewhere just joins the
    /// in-flight (or already-finished) load instead of starting a new one.
    private var settingsLoadTask: Task<Void, Never>?

    /// Engine resolution: MACCLEANER_ENGINE env override (development) →
    /// user-installed copy (shares config with the CLI) → bundled fallback.
    nonisolated static func enginePath() -> String {
        if let override = ProcessInfo.processInfo.environment["MACCLEANER_ENGINE"],
           FileManager.default.fileExists(atPath: override) {
            return override
        }
        let installed = FileManager.default.homeDirectoryForCurrentUser
            .appendingPathComponent("mac-cleaner/cleaner.py").path
        if FileManager.default.fileExists(atPath: installed) { return installed }
        return Bundle.main.path(forResource: "cleaner", ofType: "py") ?? installed
    }

    nonisolated private static func runEngine(_ args: [String]) throws -> Data {
        let process = Process()
        process.executableURL = URL(fileURLWithPath: "/usr/bin/python3")
        process.arguments = [enginePath()] + args
        // Homebrew tools (brew, docker, ...) aren't on the default GUI PATH
        var env = ProcessInfo.processInfo.environment
        env["PATH"] = "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:" + (env["PATH"] ?? "")
        process.environment = env

        let out = Pipe()
        let err = Pipe()
        process.standardOutput = out
        process.standardError = err

        let errBuffer = PipeBuffer()
        err.fileHandleForReading.readabilityHandler = { handle in
            errBuffer.append(handle.availableData)
        }

        try process.run()
        let data = out.fileHandleForReading.readDataToEndOfFile()
        process.waitUntilExit()
        err.fileHandleForReading.readabilityHandler = nil

        if process.terminationStatus != 0 {
            let msg = String(data: errBuffer.contents, encoding: .utf8)?
                .trimmingCharacters(in: .whitespacesAndNewlines) ?? ""
            throw BridgeError.engine(msg.isEmpty ? "engine exited \(process.terminationStatus)" : msg)
        }
        return data
    }

    private func run<T: Decodable>(_ type: T.Type, _ args: [String]) async throws -> T {
        let data = try await Task.detached(priority: .userInitiated) {
            try Self.runEngine(args)
        }.value
        return try JSONDecoder().decode(T.self, from: data)
    }

    private func runPlain(_ args: [String]) async throws {
        _ = try await Task.detached(priority: .userInitiated) {
            try Self.runEngine(args)
        }.value
    }

    // ── Actions ────────────────────────────────────────────────────────────────

    /// One-time (memoized) subscription that surfaces a scheduled Sparkle
    /// update (finding B1) as a native notification, reusing
    /// `NotificationManager` rather than building new plumbing. Safe to call
    /// from multiple `.task`s (MenuBarPanel, MainView) — only the first
    /// subscribes. `UpdaterManager.shared.pendingUpdateVersion` exists in
    /// both the Sparkle-enabled and `SPARKLE_DISABLED` builds (always nil in
    /// the stub), so this needs no `#if` of its own.
    func observeUpdater() {
        guard updaterCancellable == nil else { return }
        updaterCancellable = UpdaterManager.shared.$pendingUpdateVersion
            .compactMap { $0 }
            .removeDuplicates()
            .sink { [weak self] version in
                guard let self, self.notificationsEnabled else { return }
                NotificationManager.shared.post(
                    title: "MacCleaner update available",
                    body: "Version \(version) is ready to install.")
            }
    }

    func scan() async {
        isScanning = true
        defer { isScanning = false }
        do {
            report = try await run(ScanReport.self, ["scan", "--json"])
            statusMessage = nil
            lastFullScan = Date()
            // A fresh, successful scan is the signal that any prior clean's
            // progress is now stale — the target list it was tracking has
            // just been replaced. Left untouched on failure: a failed scan
            // shouldn't erase the last real clean result off the screen.
            cleaningIDs.removeAll()
            cleanedIDs.removeAll()
        } catch {
            statusMessage = "Scan failed: \(error.localizedDescription)"
        }
    }

    // ── Auto-refresh ───────────────────────────────────────────────────────────
    //
    // Two cadences on purpose: `report --json` is a couple of file reads and one
    // stat, so it can run every minute; a full `scan` shells out to `du` for
    // 70+ targets and must not.

    func startAutoRefresh() {
        lightTimer?.invalidate()
        lightTimer = Timer.scheduledTimer(withTimeInterval: 60, repeats: true) { [weak self] _ in
            Task { @MainActor in await self?.lightRefresh() }
        }
        // A tolerance-less 60s repeating timer defeats timer coalescing and
        // App Nap, so an idle menu bar app wakes the process on the dot
        // 1440x/day. 10% tolerance lets the system batch this with other
        // wake-ups (finding M7).
        lightTimer?.tolerance = 6
        scheduleFullTimer()
        if wakeObserver == nil {
            wakeObserver = NSWorkspace.shared.notificationCenter.addObserver(
                forName: NSWorkspace.didWakeNotification, object: nil, queue: .main
            ) { [weak self] _ in
                Task { @MainActor in
                    await self?.lightRefresh()
                    await self?.fullRefreshIfStale()
                }
            }
        }
        Task { await lightRefresh() }
    }

    private func scheduleFullTimer() {
        fullTimer?.invalidate()
        let interval = max(3600, fullRefreshHours * 3600)
        fullTimer = Timer.scheduledTimer(withTimeInterval: interval, repeats: true) { [weak self] _ in
            Task { @MainActor in await self?.fullRefreshIfStale() }
        }
        fullTimer?.tolerance = interval * 0.1
    }

    /// Cheap: free space and last-cleaned only. Never runs during a clean.
    func lightRefresh() async {
        guard !isCleaning else { return }
        await performLightRefresh()
    }

    /// The unguarded body of `lightRefresh()`. `clean(ids:)`/`autoCleanSafe()`
    /// call this directly for their post-clean refresh instead of going
    /// through `lightRefresh()`: at that point `isCleaning` is still `true`
    /// (it isn't cleared until the function's `defer` fires on return), so
    /// the guarded entry point would just no-op. Calling the unguarded body
    /// still guarantees no *concurrent* refresh — a timer-driven
    /// `lightRefresh()` call — can interleave with a clean in flight, since
    /// everything here runs sequentially on the main actor.
    private func performLightRefresh() async {
        guard let report = try? await run(HistoryReport.self, ["report", "--json", "-n", "1"])
        else { return }
        freeBytes = report.disk_history?.current.free_bytes
        lastCleanedAt = report.runs.last.flatMap { Self.parseTimestamp($0.timestamp) }
        diskSnapshots = report.disk_history?.snapshots ?? []
    }

    /// Full scan, debounced so a wake plus a menu-open doesn't launch two.
    func fullRefreshIfStale() async {
        // Watches both scan flags (finding "Also fix" — split isBusy): a
        // Projects re-scan in flight must still hold off the debounced
        // Dashboard refresh, and vice versa.
        guard !isCleaning, !isScanning, !isScanningProjects else { return }
        let interval = max(3600, fullRefreshHours * 3600)
        if let last = lastFullScan, Date().timeIntervalSince(last) < interval { return }
        await scan()
    }

    nonisolated static func parseTimestamp(_ raw: String) -> Date? {
        let formatter = ISO8601DateFormatter()
        formatter.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        if let d = formatter.date(from: raw) { return d }
        formatter.formatOptions = [.withInternetDateTime]
        if let d = formatter.date(from: raw) { return d }
        // The engine writes datetime.isoformat(), which has no timezone suffix.
        // Pin en_US_POSIX so this always parses the Gregorian-calendar,
        // ASCII-digit format Python wrote, regardless of the user's region
        // (e.g. a non-Gregorian calendar locale would otherwise misparse
        // these and silently corrupt the chart's X axis).
        let fallback = DateFormatter()
        fallback.locale = Locale(identifier: "en_US_POSIX")
        fallback.dateFormat = "yyyy-MM-dd'T'HH:mm:ss.SSSSSS"
        if let d = fallback.date(from: raw) { return d }
        fallback.dateFormat = "yyyy-MM-dd'T'HH:mm:ss"
        return fallback.date(from: raw)
    }

    func clean(ids: [String]) async {
        guard !ids.isEmpty else { return }
        // Re-entrancy guard (finding B3): a popover clean and a Dashboard
        // clean both drive the same shared `isCleaning`/`cleaningIDs` state,
        // so letting a second one start mid-flight means whichever finishes
        // first clears that shared state out from under the other, stranding
        // its UI. Matches the existing `setSchedule` precedent.
        guard !isCleaning else { return }
        isCleaning = true
        // Optimistic: every requested id shows a spinner immediately. Reset
        // cleanedIDs too, in case an earlier clean's reconciled checkmarks
        // are still showing (scan() normally clears them, but a batch that
        // targets an id no prior scan cleared should never inherit a stale
        // "done" from a previous run).
        cleaningIDs = Set(ids)
        cleanedIDs.removeAll()
        // Optimistically cleared here so a success path never has to
        // remember to do it (see the property's doc comment for why this
        // can't just piggyback on `statusMessage`).
        lastCleanFailed = nil
        defer { isCleaning = false }
        do {
            var args = ["clean", "--targets", ids.joined(separator: ","), "--yes"]
            if deleteMode == "trash" { args.append("--trash") }
            args.append("--json")
            lastClean = try await run(CleanResult.self, args)
            statusMessage = nil
            // Reconcile from the real per-item results now that the process
            // has exited — only ids the engine actually reported on move to
            // cleanedIDs; anything it stayed silent on (shouldn't happen,
            // but never assume) keeps its spinner rather than being guessed
            // as done.
            let resultIDs = Set(lastClean?.items.map(\.id) ?? [])
            cleaningIDs.subtract(resultIDs)
            cleanedIDs.formUnion(resultIDs)
            // Settings must be loaded before this check even in a menu-bar-only
            // session (finding I4) — see ensureSettingsLoaded().
            await ensureSettingsLoaded().value
            if notificationsEnabled, let result = lastClean {
                NotificationManager.shared.post(
                    title: "MacCleaner freed \(result.freed_human)",
                    body: "\(result.items.filter { $0.status != "skipped" }.count) items cleaned")
            }
        } catch {
            statusMessage = "Clean failed: \(error.localizedDescription)"
            // B2: a failed clean must never leave the previous run's success
            // displayed as if it were still current.
            lastClean = nil
            lastCleanFailed = error.localizedDescription
            lastCleanFailedAt = Date()
            // The process never produced per-item results, so nothing can
            // honestly move to cleanedIDs — just stop showing spinners for it.
            cleaningIDs.removeAll()
        }
        await scan()
        await loadHistory()
        // One refresh covers both "Free disk" and "Last cleaned" in the menu
        // bar, instead of leaving them up to 60s stale (finding M12). Goes
        // through the unguarded body, not lightRefresh() — isCleaning is
        // still true here (the `defer` above only clears it once this
        // function returns), so lightRefresh()'s own guard would swallow it.
        await performLightRefresh()
    }

    func autoCleanSafe() async {
        // Re-entrancy guard (finding B3) — see clean(ids:)'s comment.
        guard !isCleaning else { return }
        isCleaning = true
        lastCleanFailed = nil
        defer { isCleaning = false }
        do {
            lastClean = try await run(CleanResult.self, ["clean", "--yes", "--json"])
            statusMessage = nil
            // Settings must be loaded before this check even in a menu-bar-only
            // session (finding I4) — see ensureSettingsLoaded().
            await ensureSettingsLoaded().value
            if notificationsEnabled, let result = lastClean {
                NotificationManager.shared.post(
                    title: "MacCleaner freed \(result.freed_human)",
                    body: "\(result.items.filter { $0.status != "skipped" }.count) items cleaned")
            }
        } catch {
            statusMessage = "Clean failed: \(error.localizedDescription)"
            // B2: a failed clean must never leave the previous run's success
            // displayed as if it were still current.
            lastClean = nil
            lastCleanFailed = error.localizedDescription
            lastCleanFailedAt = Date()
        }
        await scan()
        await loadHistory()
        // One refresh covers both "Free disk" and "Last cleaned" in the menu
        // bar, instead of leaving them up to 60s stale (finding M12). Goes
        // through the unguarded body, not lightRefresh() — isCleaning is
        // still true here (the `defer` above only clears it once this
        // function returns), so lightRefresh()'s own guard would swallow it.
        await performLightRefresh()
    }

    func scanProjects() async {
        isScanningProjects = true
        defer { isScanningProjects = false }
        do {
            projects = try await run(ProjectsReport.self, ["projects", "--json"])
            statusMessage = nil
        } catch {
            statusMessage = "Project scan failed: \(error.localizedDescription)"
        }
    }

    func cleanProjects(ids: [String]) async {
        guard !ids.isEmpty else { return }
        // Re-entrancy guard (finding B3) — see clean(ids:)'s comment.
        guard !isCleaning else { return }
        isCleaning = true
        lastCleanFailed = nil
        defer { isCleaning = false }
        do {
            var args = ["projects", "--clean", "--targets", ids.joined(separator: ","), "--yes"]
            if deleteMode == "trash" { args.append("--trash") }
            args.append("--json")
            lastClean = try await run(CleanResult.self, args)
            statusMessage = nil
        } catch {
            statusMessage = "Clean failed: \(error.localizedDescription)"
            // B2: a failed clean must never leave the previous run's success
            // displayed as if it were still current.
            lastClean = nil
            lastCleanFailed = error.localizedDescription
            lastCleanFailedAt = Date()
        }
        await scanProjects()
        await loadHistory()
    }

    func loadHistory() async {
        do {
            history = try await run(HistoryReport.self, ["report", "--json", "-n", "20"]).runs.reversed()
        } catch {
            history = []
        }
    }

    /// Kicks off `loadSettings()` at most once per app run and returns the
    /// shared task, so a caller can either fire-and-forget it (the menu bar's
    /// `.task`, which must not block on a subprocess at launch) or `await
    /// .value` to guarantee settings are current before acting on them (the
    /// notification checks in `clean`/`autoCleanSafe`) — without reloading
    /// settings again just because the menu happened to open first
    /// (finding I4).
    @discardableResult
    func ensureSettingsLoaded() -> Task<Void, Never> {
        if let existing = settingsLoadTask { return existing }
        let task = Task { await loadSettings() }
        settingsLoadTask = task
        return task
    }

    func loadSettings() async {
        do {
            categories = try await run(CategoriesReport.self, ["categories", "--json"]).categories
            let cfg = try await run(EngineConfig.self, ["config", "show"])
            deleteMode = cfg.delete_mode ?? "rm"
            notificationsEnabled = cfg.notifications ?? true
            lowDiskAlertsEnabled = cfg.low_disk_alerts ?? true
            lowDiskThresholdGB = cfg.low_disk_threshold_gb ?? 10
            fullRefreshHours = cfg.full_refresh_hours ?? 6
        } catch {
            statusMessage = "Could not load settings: \(error.localizedDescription)"
        }
    }

    func setCategory(_ name: String, enabled: Bool) async {
        do {
            try await runPlain(["config", enabled ? "enable" : "disable", name])
            categories = (try? await run(CategoriesReport.self, ["categories", "--json"]).categories) ?? categories
        } catch {
            statusMessage = "Config change failed: \(error.localizedDescription)"
        }
    }

    func setDeleteMode(_ mode: String) async {
        do {
            try await runPlain(["config", "set", "delete_mode", mode])
            deleteMode = mode
        } catch {
            statusMessage = "Config change failed: \(error.localizedDescription)"
        }
    }

    func setNotifications(_ on: Bool) async {
        do {
            try await runPlain(["config", "set", "notifications", on ? "true" : "false"])
            notificationsEnabled = on
        } catch {
            statusMessage = "Config change failed: \(error.localizedDescription)"
        }
    }

    func setLowDiskAlerts(_ on: Bool) async {
        do {
            try await runPlain(["config", "set", "low_disk_alerts", on ? "true" : "false"])
            lowDiskAlertsEnabled = on
        } catch {
            statusMessage = "Config change failed: \(error.localizedDescription)"
        }
    }

    func setLowDiskThreshold(_ gb: Double) async {
        do {
            try await runPlain(["config", "set", "low_disk_threshold_gb",
                                String(format: "%g", gb)])
            lowDiskThresholdGB = gb
        } catch {
            statusMessage = "Config change failed: \(error.localizedDescription)"
        }
    }

    func loadSchedule() async {
        do {
            scheduleStatus = try await run(ScheduleStatus.self, ["schedule", "status", "--json"])
            scheduleSupported = true
        } catch {
            // An older engine exits 2 on the unknown subcommand; treat any
            // failure here as "can't manage scheduling", not an error banner.
            scheduleStatus = nil
            scheduleSupported = false
        }
    }

    func setSchedule(_ choice: String) async {
        guard !isSchedulingBusy else { return }
        isSchedulingBusy = true
        pendingSchedule = choice
        defer { isSchedulingBusy = false; pendingSchedule = nil }
        do {
            try await runPlain(["schedule", choice])
            await loadSchedule()
        } catch {
            statusMessage = "Schedule change failed: \(error.localizedDescription)"
            await loadSchedule()   // revert the picker to the real state
        }
    }
}

import Foundation
import AppKit

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

struct ProjectArtifact: Codable, Identifiable, Hashable {
    let id: String
    let path: String
    let kind: String
    let project: String
    let age_days: Int
    let size_bytes: Int
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
    var id: String { timestamp }
}

struct DiskCurrent: Codable {
    let free_bytes: Int
    let total_bytes: Int
}

struct DiskSnapshot: Codable, Identifiable {
    let ts: String
    let disk_free_bytes: Int
    let disk_total_bytes: Int
    var id: String { ts }
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
    @Published var isBusy = false
    @Published var isCleaning = false
    @Published var statusMessage: String?
    @Published var lastClean: CleanResult?
    @Published var lastCleanedAt: Date?
    @Published var freeBytes: Int?
    @Published var diskSnapshots: [DiskSnapshot] = []

    private var lightTimer: Timer?
    private var fullTimer: Timer?
    private var lastFullScan: Date?
    private var wakeObserver: NSObjectProtocol?
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

    func scan() async {
        isBusy = true
        defer { isBusy = false }
        do {
            report = try await run(ScanReport.self, ["scan", "--json"])
            statusMessage = nil
            lastFullScan = Date()
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
        guard !isCleaning, !isBusy else { return }
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
        let fallback = DateFormatter()
        fallback.dateFormat = "yyyy-MM-dd'T'HH:mm:ss.SSSSSS"
        if let d = fallback.date(from: raw) { return d }
        fallback.dateFormat = "yyyy-MM-dd'T'HH:mm:ss"
        return fallback.date(from: raw)
    }

    func clean(ids: [String]) async {
        guard !ids.isEmpty else { return }
        isCleaning = true
        defer { isCleaning = false }
        do {
            var args = ["clean", "--targets", ids.joined(separator: ","), "--yes"]
            if deleteMode == "trash" { args.append("--trash") }
            args.append("--json")
            lastClean = try await run(CleanResult.self, args)
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
        isCleaning = true
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
        isBusy = true
        defer { isBusy = false }
        do {
            projects = try await run(ProjectsReport.self, ["projects", "--json"])
            statusMessage = nil
        } catch {
            statusMessage = "Project scan failed: \(error.localizedDescription)"
        }
    }

    func cleanProjects(ids: [String]) async {
        guard !ids.isEmpty else { return }
        isCleaning = true
        defer { isCleaning = false }
        do {
            var args = ["projects", "--clean", "--targets", ids.joined(separator: ","), "--yes"]
            if deleteMode == "trash" { args.append("--trash") }
            args.append("--json")
            lastClean = try await run(CleanResult.self, args)
            statusMessage = nil
        } catch {
            statusMessage = "Clean failed: \(error.localizedDescription)"
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
        do {
            try await runPlain(["schedule", choice])
            await loadSchedule()
        } catch {
            statusMessage = "Schedule change failed: \(error.localizedDescription)"
            await loadSchedule()   // revert the picker to the real state
        }
    }
}

import SwiftUI
import AppKit

@main
struct MacCleanerApp: App {
    @StateObject private var bridge = CleanerBridge()

    init() {
        NotificationManager.shared.requestAuthorization()

        // NSAlert and other AppKit surfaces render NSApp.applicationIconImage.
        // Resolving it from CFBundleIconFile can silently fall back to the
        // generic icon when icon services has a stale cache entry for this
        // bundle path (common for ad-hoc-signed bundles replaced in place by
        // install.sh). Loading the icns ourselves makes the icon deterministic.
        if let path = Bundle.main.path(forResource: "MacCleaner", ofType: "icns"),
           let image = NSImage(contentsOfFile: path) {
            NSApplication.shared.applicationIconImage = image
        }
    }

    var body: some Scene {
        Window("MacCleaner", id: "main") {
            MainView()
                .environmentObject(bridge)
                .frame(minWidth: 720, minHeight: 480)
        }
        .defaultSize(width: 800, height: 580)

        MenuBarExtra {
            MenuBarContent()
                .environmentObject(bridge)
                .task {
                    // Fire-and-forget: a menu-bar-only session (the user never
                    // opens the main window) must still have real settings —
                    // e.g. a disabled `notifications` — before Auto-Clean Safe
                    // can post a banner (finding I4). Memoized, so this is a
                    // no-op on every later menu open.
                    bridge.ensureSettingsLoaded()
                    await bridge.lightRefresh()
                    await bridge.fullRefreshIfStale()
                }
        } label: {
            if let report = bridge.report {
                Text("🧹 \(report.total_reclaimable_human)")
            } else {
                Text("🧹")
            }
        }
    }
}

struct MenuBarContent: View {
    @EnvironmentObject var bridge: CleanerBridge
    @Environment(\.openWindow) private var openWindow

    var body: some View {
        if let report = bridge.report {
            Text("Reclaimable: \(report.total_reclaimable_human)")
        }
        if let free = bridge.freeBytes {
            Text("Free disk: \(ByteCountFormatter.string(fromByteCount: Int64(free), countStyle: .file))")
        } else if let stats = bridge.report?.disk_stats {
            Text("Free disk: \(ByteCountFormatter.string(fromByteCount: Int64(stats.free_bytes), countStyle: .file))")
        }
        Text("Last cleaned: \(Self.lastCleanedText(bridge.lastCleanedAt))")
        Divider()
        Button(bridge.isBusy ? "Scanning…" : "Scan Now") {
            Task { await bridge.scan() }
        }
        .disabled(bridge.isBusy || bridge.isCleaning)

        Button("Auto-Clean Safe Items…") {
            let alert = NSAlert()
            alert.messageText = "Auto-Clean Safe Items?"
            alert.informativeText = "Deletes all safe items without further prompts. Review-level items are never touched."
            alert.addButton(withTitle: "Clean Now")
            alert.addButton(withTitle: "Cancel")
            alert.alertStyle = .warning
            alert.icon = NSApplication.shared.applicationIconImage
            if alert.runModal() == .alertFirstButtonReturn {
                Task { await bridge.autoCleanSafe() }
            }
        }
        .disabled(bridge.isCleaning)

        Divider()

        Button("Open MacCleaner") {
            openWindow(id: "main")
            NSApp.activate(ignoringOtherApps: true)
        }

        Divider()

        Button("Quit MacCleaner") {
            NSApp.terminate(nil)
        }
    }

    static func lastCleanedText(_ date: Date?) -> String {
        guard let date else { return "Never" }
        let formatter = RelativeDateTimeFormatter()
        formatter.unitsStyle = .full
        return formatter.localizedString(for: date, relativeTo: Date())
    }
}

struct MainView: View {
    @EnvironmentObject var bridge: CleanerBridge

    var body: some View {
        TabView {
            DashboardView()
                .tabItem { Label("Dashboard", systemImage: "gauge") }
            ProjectsView()
                .tabItem { Label("Projects", systemImage: "folder") }
            HistoryView()
                .tabItem { Label("History", systemImage: "clock") }
            SettingsView()
                .tabItem { Label("Settings", systemImage: "gearshape") }
        }
        .task {
            bridge.startAutoRefresh()
            // Load config (incl. full_refresh_hours) in the background so launch
            // never waits on a subprocess; fullRefreshHours' didSet reschedules
            // the periodic timer once the real value comes back. Memoized —
            // joins the menu bar's load if that already kicked one off.
            bridge.ensureSettingsLoaded()
            if bridge.report == nil {
                await bridge.scan()
            }
        }
        .overlay(alignment: .bottom) {
            if let message = bridge.statusMessage {
                Text(message)
                    .font(.callout)
                    .padding(8)
                    .background(.red.opacity(0.85), in: RoundedRectangle(cornerRadius: 8))
                    .foregroundColor(.white)
                    .padding(.bottom, 12)
                    .transition(.opacity)
            }
        }
    }
}

import SwiftUI
import AppKit

@main
struct MacCleanerApp: App {
    @StateObject private var bridge = CleanerBridge()

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
            if let stats = report.disk_stats {
                Text("Free disk: \(ByteCountFormatter.string(fromByteCount: Int64(stats.free_bytes), countStyle: .file))")
            }
            Divider()
        }
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

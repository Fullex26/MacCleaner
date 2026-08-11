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
            MenuBarPanel()
                .environmentObject(bridge)
        } label: {
            if let report = bridge.report {
                Text("🧹 \(report.total_reclaimable_human)")
            } else {
                Text("🧹")
            }
        }
        .menuBarExtraStyle(.window)
    }
}

/// The four top-level sections reachable from the sidebar. `⌘1`–`⌘4` jump
/// straight to a section (see the hidden shortcut buttons in `MainView`).
enum AppSection: String, CaseIterable, Identifiable {
    case dashboard, projects, history, settings

    var id: String { rawValue }

    var title: String {
        switch self {
        case .dashboard: return "Dashboard"
        case .projects: return "Projects"
        case .history: return "History"
        case .settings: return "Settings"
        }
    }

    var symbolName: String {
        switch self {
        case .dashboard: return "gauge"
        case .projects: return "folder"
        case .history: return "clock"
        case .settings: return "gearshape"
        }
    }
}

struct MainView: View {
    @EnvironmentObject var bridge: CleanerBridge
    @State private var selection: AppSection? = .dashboard

    var body: some View {
        NavigationSplitView {
            List(AppSection.allCases, selection: $selection) { section in
                Label(section.title, systemImage: section.symbolName)
                    .tag(section)
            }
            .navigationSplitViewColumnWidth(min: 170, ideal: 190, max: 240)
            .listStyle(.sidebar)
        } detail: {
            Group {
                switch selection ?? .dashboard {
                case .dashboard: DashboardView()
                case .projects: ProjectsView()
                case .history: HistoryView()
                case .settings: SettingsView()
                }
            }
            .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
        }
        .tint(.accentCyan)
        .background(keyboardShortcuts)
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

    /// Invisible buttons purely to register `⌘1`–`⌘4` as section-switch
    /// shortcuts without needing a `Commands` scene builder (out of scope —
    /// `MacCleanerApp`'s `Scene` isn't touched by this view).
    private var keyboardShortcuts: some View {
        Group {
            Button("") { selection = .dashboard }.keyboardShortcut("1", modifiers: .command)
            Button("") { selection = .projects }.keyboardShortcut("2", modifiers: .command)
            Button("") { selection = .history }.keyboardShortcut("3", modifiers: .command)
            Button("") { selection = .settings }.keyboardShortcut("4", modifiers: .command)
        }
        .frame(width: 0, height: 0)
        .opacity(0)
        .accessibilityHidden(true)
    }
}

import SwiftUI
import AppKit

/// The `.window`-style `MenuBarExtra` content — a compact glass panel that
/// replaces the old plain `MenuBarContent` menu (design spec §3, "the
/// flagship of the new design"). Fixed 300pt wide: disk-usage ring, the
/// reclaimable hero number, the top categories by size, a one-click safe
/// clean, and footer links.
struct MenuBarPanel: View {
    @EnvironmentObject var bridge: CleanerBridge
    @Environment(\.openWindow) private var openWindow

    private static let panelWidth: CGFloat = 300

    /// Top 3 categories by summed size — same grouping idea as
    /// DashboardView's `groupedTargets`, reduced to what a compact panel
    /// needs (just a name + total, capped at 3).
    private var topCategories: [(category: String, bytes: Int)] {
        guard let targets = bridge.report?.targets else { return [] }
        let visible = targets.filter { ($0.exists ?? true) && $0.size_bytes > 0 }
        let groups = Dictionary(grouping: visible, by: \.category)
        return groups
            .map { (category: $0.key, bytes: $0.value.reduce(0) { $0 + $1.size_bytes }) }
            .sorted { $0.bytes > $1.bytes }
            .prefix(3)
            .map { $0 }
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            if bridge.report != nil {
                summaryRow
                if !topCategories.isEmpty {
                    categoryList
                }
                actionArea
            } else {
                emptyState
            }
            Divider()
            footer
        }
        .padding(16)
        .frame(width: Self.panelWidth)
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
    }

    // MARK: - Summary (disk ring + hero number)

    private var summaryRow: some View {
        HStack(spacing: 14) {
            diskRing
            VStack(alignment: .leading, spacing: 2) {
                Text(bridge.report?.total_reclaimable_human ?? "—")
                    .font(.heroNumber)
                    .contentTransition(.numericText())
                    .animation(Motion.standard, value: bridge.report?.total_reclaimable_human)
                Text(freeCaption)
                    .font(.metaCaption)
                    .foregroundStyle(.secondary)
            }
            Spacer(minLength: 0)
        }
    }

    private var percentUsed: Double {
        guard let stats = bridge.report?.disk_stats else { return 0 }
        return min(max(stats.percent_used / 100, 0), 1)
    }

    private var diskRing: some View {
        ZStack {
            Circle()
                .trim(from: 0, to: 1)
                .stroke(Color.gradientBarTrack, style: StrokeStyle(lineWidth: 5, lineCap: .round))
            Circle()
                .trim(from: 0, to: percentUsed)
                .stroke(
                    LinearGradient(colors: [.accentCyan, .accentIndigo], startPoint: .top, endPoint: .bottom),
                    style: StrokeStyle(lineWidth: 5, lineCap: .round)
                )
                .rotationEffect(.degrees(-90))
                .animation(Motion.standard, value: percentUsed)
            Text("\(Int(percentUsed * 100))%")
                .font(.system(size: 10, weight: .semibold, design: .monospaced))
        }
        .frame(width: 44, height: 44)
    }

    private var freeCaption: String {
        guard let stats = bridge.report?.disk_stats else { return "reclaimable" }
        let free = ByteCountFormatter.string(fromByteCount: Int64(stats.free_bytes), countStyle: .file)
        let total = ByteCountFormatter.string(fromByteCount: Int64(stats.total_bytes), countStyle: .file)
        return "\(free) free of \(total)"
    }

    // MARK: - Top categories

    private var categoryList: some View {
        VStack(spacing: 6) {
            ForEach(topCategories, id: \.category) { entry in
                HStack(spacing: 8) {
                    Circle()
                        .fill(categoryColor(entry.category))
                        .frame(width: 7, height: 7)
                    Text(entry.category.capitalized)
                        .font(.rowLabel)
                    Spacer()
                    Text(ByteCountFormatter.string(fromByteCount: Int64(entry.bytes), countStyle: .file))
                        .font(.metaCaption)
                        .foregroundStyle(.secondary)
                }
                .padding(.horizontal, 10)
                .padding(.vertical, 6)
                .glassPanel(cornerRadius: 8)
            }
        }
    }

    // MARK: - Action

    @ViewBuilder
    private var actionArea: some View {
        if bridge.isCleaning {
            HStack(spacing: 8) {
                ProgressView()
                    .controlSize(.small)
                Text("Cleaning…")
                    .foregroundStyle(.secondary)
            }
            .frame(maxWidth: .infinity, alignment: .center)
        } else if let result = bridge.lastClean {
            HStack(spacing: 6) {
                Image(systemName: "checkmark.circle.fill")
                    .foregroundStyle(.green)
                Text("Freed \(result.freed_human) \u{2713}")
                    .foregroundStyle(.green)
            }
            .animation(Motion.standard, value: result.freed_human)
            .frame(maxWidth: .infinity, alignment: .center)
        } else {
            Button {
                confirmAndCleanSafe()
            } label: {
                Text("Clean safe items")
                    .frame(maxWidth: .infinity)
            }
            .buttonStyle(.borderedProminent)
            .tint(.accentCyan)
        }
    }

    /// The exact confirm flow from the old `MenuBarContent`'s "Auto-Clean Safe
    /// Items…" button, moved across verbatim (including the applicationIcon
    /// line) — only the trigger changed, from a menu item to this button.
    private func confirmAndCleanSafe() {
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

    // MARK: - Empty state

    @ViewBuilder
    private var emptyState: some View {
        VStack(spacing: 10) {
            if bridge.isBusy {
                ProgressView("Scanning…")
            } else {
                Text("No scan yet")
                    .foregroundStyle(.secondary)
                Button("Scan") {
                    Task { await bridge.scan() }
                }
                .buttonStyle(.borderedProminent)
                .tint(.accentCyan)
            }
        }
        .frame(maxWidth: .infinity, minHeight: 80)
    }

    // MARK: - Footer

    private var footer: some View {
        HStack {
            Button("Open MacCleaner") {
                openWindow(id: "main")
                NSApp.activate(ignoringOtherApps: true)
            }
            Spacer()
            Button("Quit") {
                NSApp.terminate(nil)
            }
        }
        .buttonStyle(.link)
    }
}

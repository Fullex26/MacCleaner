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
    @ObservedObject private var updater = UpdaterManager.shared

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
            updateAvailableRow
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
            bridge.observeUpdater()
            await bridge.lightRefresh()
            await bridge.fullRefreshIfStale()
        }
    }

    // MARK: - Update available (B1)

    /// Discoverable affordance for a scheduled update Sparkle found but
    /// couldn't put in front of the user itself (see `UpdaterManager`'s
    /// `standardUserDriverWillHandleShowingUpdate`) — this is the
    /// menu-bar-first surface most users will actually see, since the app
    /// has no Dock icon. Tapping re-invokes Sparkle as a user-initiated
    /// check, which shows its alert normally.
    @ViewBuilder
    private var updateAvailableRow: some View {
        if let version = updater.pendingUpdateVersion {
            Button {
                updater.checkForUpdates()
            } label: {
                HStack(spacing: 8) {
                    Image(systemName: "arrow.down.circle.fill")
                        .foregroundStyle(Color.accentCyan)
                    Text("Update to \(version) available")
                        .font(.rowLabel)
                    Spacer()
                    Image(systemName: "chevron.right")
                        .font(.caption2)
                        .foregroundStyle(.secondary)
                }
                .padding(.horizontal, 10)
                .padding(.vertical, 6)
                .glassPanel(cornerRadius: 8)
            }
            .buttonStyle(.plain)
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
            // Deliberately not `.metaCaption`: that token is `.caption`
            // (~10pt regular) sized for row/list text, not a 44pt ring with
            // only a ~34pt clear interior once the 5pt stroke is subtracted —
            // at that size a regular weight loses contrast against the
            // gradient stroke right behind it. Same size/weight as
            // `Font.sectionLabel` (10pt semibold), but SwiftUI's `Font` has
            // no API to bolt a monospaced *design* onto an existing token
            // after the fact, and digits jittering as the percentage changes
            // reads worse here than the duplication — so this stays a
            // one-off `.system(...)` call with `design: .monospaced` added,
            // same rationale as `ReviewBadge` (pre-`Font.sectionLabel`)
            // layering a custom weight on a shared token rather than reusing
            // it unmodified.
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
                    Text(categoryDisplayName(entry.category))
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
    //
    // The CTA must never permanently disappear: `bridge.lastClean` is shared,
    // process-lifetime state (Dashboard's footer reads it too) that is never
    // reset after a clean, and this is an `LSUIElement` app that can run for
    // weeks without a relaunch. So the button always stays put; only a
    // *transient* confirmation caption appears below it, gated on freshness
    // rather than mere presence of a result.

    private var actionArea: some View {
        VStack(spacing: 6) {
            Button {
                confirmAndCleanSafe()
            } label: {
                HStack(spacing: 6) {
                    if bridge.isCleaning {
                        ProgressView()
                            .controlSize(.small)
                    }
                    Text(bridge.isCleaning ? "Cleaning…" : "Clean safe items")
                }
                .frame(maxWidth: .infinity)
            }
            .buttonStyle(.borderedProminent)
            .tint(.accentCyan)
            .disabled(bridge.isCleaning)

            freshCleanCaption
        }
    }

    /// Shown only while the last clean result is "fresh" (finished in
    /// roughly the last 2 minutes) — never a permanent replacement for the
    /// button above. Freshness is derived from `bridge.lastCleanedAt`
    /// (success) / `bridge.lastCleanFailedAt` (failure) rather than mere
    /// presence of `bridge.lastClean`/`lastCleanFailed`, which — being
    /// shared with Dashboard — are never cleared on their own and would
    /// otherwise make this caption stick around forever. `TimelineView`
    /// re-evaluates once a minute while the popover is open, and picks the
    /// right answer immediately whenever it's freshly opened; this
    /// deliberately never mutates bridge state itself.
    ///
    /// B2: `bridge.lastClean` is set to `nil` in the catch block of every
    /// clean path, so a failed clean can never fall through to the success
    /// branch below and render as if the previous run's result was still
    /// current — it renders its own error state instead.
    private var freshCleanCaption: some View {
        TimelineView(.periodic(from: .now, by: 60)) { context in
            if let failure = bridge.lastCleanFailed,
               let failedAt = bridge.lastCleanFailedAt,
               context.date.timeIntervalSince(failedAt) < 120 {
                HStack(spacing: 6) {
                    Image(systemName: "exclamationmark.triangle.fill")
                        .foregroundStyle(.red)
                    Text(failure)
                        .foregroundStyle(.red)
                        .lineLimit(2)
                }
                .frame(maxWidth: .infinity, alignment: .center)
            } else if let result = bridge.lastClean,
               let cleanedAt = bridge.lastCleanedAt,
               context.date.timeIntervalSince(cleanedAt) < 120 {
                HStack(spacing: 6) {
                    Image(systemName: "checkmark.circle.fill")
                        .foregroundStyle(.green)
                    Text("Freed \(result.freed_human) \u{2713}")
                        .foregroundStyle(.green)
                }
                .animation(Motion.standard, value: result.freed_human)
                .frame(maxWidth: .infinity, alignment: .center)
            }
        }
    }

    /// The exact confirm flow from the old `MenuBarContent`'s "Auto-Clean Safe
    /// Items…" button, moved across verbatim (including the applicationIcon
    /// line) — only the trigger changed, from a menu item to this button.
    /// `.window`-style `MenuBarExtra` panels are non-activating, so without
    /// explicitly activating first, `runModal()` can render the alert behind
    /// the frontmost app — an invisible hang. Mirrors the footer's "Open
    /// MacCleaner" button, which already does this before showing the window.
    private func confirmAndCleanSafe() {
        let alert = NSAlert()
        alert.messageText = "Auto-Clean Safe Items?"
        alert.informativeText = "Deletes all safe items without further prompts. Review-level items are never touched."
        alert.addButton(withTitle: "Clean Now")
        alert.addButton(withTitle: "Cancel")
        alert.alertStyle = .warning
        alert.icon = NSApplication.shared.applicationIconImage
        NSApp.activate(ignoringOtherApps: true)
        if alert.runModal() == .alertFirstButtonReturn {
            Task { await bridge.autoCleanSafe() }
        }
    }

    // MARK: - Empty state

    @ViewBuilder
    private var emptyState: some View {
        VStack(spacing: 10) {
            if bridge.isScanning {
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

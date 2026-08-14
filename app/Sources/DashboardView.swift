import SwiftUI
import AppKit

struct DashboardView: View {
    @EnvironmentObject var bridge: CleanerBridge
    @State private var selection = Set<String>()
    @State private var confirmClean = false
    /// Snapshot of `selection` taken the moment the confirmation dialog is
    /// raised (finding B6) — a background refresh can reseed `selection`
    /// between raising the dialog and the user tapping confirm, so both the
    /// dialog's own text and the actual destructive action read from this
    /// frozen copy instead of the live, possibly-changed `selection`.
    @State private var pendingIDs: [String] = []

    private var targets: [ScanTarget] {
        bridge.report?.targets.filter { ($0.exists ?? true) && ($0.size_bytes > 0 || $0.safe) } ?? []
    }

    private var groupedTargets: [(category: String, items: [ScanTarget])] {
        let groups = Dictionary(grouping: targets, by: \.category)
        return groups
            .map { (category: $0.key, items: $0.value.sorted { $0.size_bytes > $1.size_bytes }) }
            .sorted { lhs, rhs in
                lhs.items.reduce(0) { $0 + $1.size_bytes } > rhs.items.reduce(0) { $0 + $1.size_bytes }
            }
    }

    private var selectedBytes: Int {
        targets.filter { selection.contains($0.id) }.reduce(0) { $0 + $1.size_bytes }
    }

    /// Bytes for the frozen `pendingIDs` snapshot (B6), not the live
    /// `selection` — used by the confirmation dialog's own title/action so
    /// they always agree with each other and with what the user first saw.
    private var pendingBytes: Int {
        let ids = Set(pendingIDs)
        return targets.filter { ids.contains($0.id) }.reduce(0) { $0 + $1.size_bytes }
    }

    var body: some View {
        VStack(spacing: 0) {
            header
            DiskTrendView()
                .padding(.horizontal)
                .padding(.bottom, 8)
            largeFilesSection
                .padding(.horizontal)
                .padding(.bottom, 8)
            Divider()
            content
            Divider()
            footer
        }
    }

    /// Self-contained widget, same shape as `DiskTrendView` above it: it
    /// fetches its own data (`storage-insights --json`, independent of the
    /// main reclaimable-targets `scan`) and renders regardless of whether
    /// `bridge.report` has ever been loaded. Read-only — "Reveal in Finder"
    /// is the only action, never a delete/clean path.
    private var largeFilesSection: some View {
        VStack(alignment: .leading, spacing: 8) {
            // Same uppercase/tracked-caption recipe as `SettingsSection`'s
            // title (`Font.sectionLabel`) — the app's one named "section
            // heading" token; there is no separate `DesignSystem.Typography`
            // namespace.
            Text("Large Files".uppercased())
                .font(.sectionLabel)
                .tracking(0.5)
                .foregroundStyle(.secondary)

            if let entries = bridge.storageInsights?.entries, !entries.isEmpty {
                VStack(alignment: .leading, spacing: 2) {
                    ForEach(entries) { entry in
                        LargeFileRow(entry: entry)
                    }
                }
            } else if bridge.isScanningStorageInsights {
                ProgressView()
                    .frame(maxWidth: .infinity, alignment: .center)
                    .padding(.vertical, 8)
            } else {
                Text("No files ≥100 MB found in Documents, Downloads, or Desktop.")
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(16)
        .glassPanel()
        .task { await bridge.scanStorageInsights() }
    }

    private var header: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack(alignment: .firstTextBaseline, spacing: 8) {
                Text(bridge.report?.total_reclaimable_human ?? "—")
                    .font(.heroNumber)
                    .contentTransition(.numericText())
                    .animation(Motion.standard, value: bridge.report?.total_reclaimable_human)
                Text("reclaimable")
                    .font(.title3)
                    .foregroundStyle(.secondary)
                Spacer()
                Button {
                    Task { await bridge.scan() }
                } label: {
                    Label(bridge.isScanning ? "Scanning…" : "Scan", systemImage: "arrow.clockwise")
                }
                .disabled(bridge.isScanning || bridge.isCleaning)
            }
            if let stats = bridge.report?.disk_stats {
                Text(diskMetaText(stats))
                    .font(.metaCaption)
                    .foregroundStyle(.secondary)
                GradientBar(fraction: stats.percent_used / 100)
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding()
    }

    private func diskMetaText(_ stats: DiskStats) -> String {
        let free = ByteCountFormatter.string(fromByteCount: Int64(stats.free_bytes), countStyle: .file)
        let total = ByteCountFormatter.string(fromByteCount: Int64(stats.total_bytes), countStyle: .file)
        return "\(free) free of \(total) · last scan \(lastScanText)"
    }

    private var lastScanText: String {
        guard let ts = bridge.report?.timestamp, let date = CleanerBridge.parseTimestamp(ts) else {
            return "—"
        }
        let formatter = RelativeDateTimeFormatter()
        formatter.unitsStyle = .full
        return formatter.localizedString(for: date, relativeTo: Date())
    }

    @ViewBuilder
    private var content: some View {
        if bridge.report == nil {
            Spacer()
            if bridge.isScanning {
                ProgressView("Scanning your Mac…")
            } else {
                Text("Press Scan to see what can be cleaned.")
                    .foregroundStyle(.secondary)
            }
            Spacer()
        } else {
            bulkBar
            Divider()
            List {
                ForEach(groupedTargets, id: \.category) { group in
                    Section {
                        ForEach(group.items) { target in
                            TargetRow(target: target, isSelected: binding(for: target.id))
                        }
                    } header: {
                        HStack {
                            Text(categoryDisplayName(group.category))
                            Spacer()
                            let bytes = group.items.reduce(0) { $0 + $1.size_bytes }
                            Text(ByteCountFormatter.string(fromByteCount: Int64(bytes), countStyle: .file))
                                .foregroundStyle(.secondary)
                        }
                    }
                }
            }
            .listStyle(.inset)
            .onAppear { seedSelection() }
            .onChange(of: bridge.report?.timestamp) { _ in seedSelection() }
        }
    }

    /// Bulk-select bar above the target list: All / None / Safe only, plus a
    /// running "N of M" count. `.buttonStyle(.link)` keeps these looking like
    /// inline text actions rather than a row of chrome buttons.
    private var bulkBar: some View {
        HStack(spacing: 12) {
            Text("Select:").font(.metaCaption).foregroundStyle(.secondary)
            Button("All")  { selection = Set(targets.map(\.id)) }
            Button("None") { selection.removeAll() }
            Button("Safe only") { seedSelection() }
            Spacer()
            Text("\(selection.count) of \(targets.count)")
                .font(.metaCaption).monospacedDigit().foregroundStyle(.secondary)
        }
        .buttonStyle(.link)
        .padding(.horizontal).padding(.vertical, 6)
    }

    private var footer: some View {
        HStack {
            footerStatus
            Spacer()
            Button {
                // B6: freeze the selection the dialog will describe and act
                // on — a background refresh (seedSelection() on a fresh
                // scan) can reseed `selection` while the dialog is open, and
                // without this snapshot the eventual delete would silently
                // act on whatever `selection` had become by the time the
                // user tapped confirm, not the count/bytes they read.
                pendingIDs = Array(selection)
                confirmClean = true
            } label: {
                Label("Clean Selected", systemImage: "trash")
            }
            .buttonStyle(.borderedProminent)
            .keyboardShortcut(.defaultAction)
            .disabled(selection.isEmpty || bridge.isCleaning || bridge.isScanning)
            .confirmationDialog(
                "Delete \(pendingIDs.count) items (\(ByteCountFormatter.string(fromByteCount: Int64(pendingBytes), countStyle: .file)))?",
                isPresented: $confirmClean
            ) {
                Button(bridge.deleteMode == "trash" ? "Move to Trash" : "Delete", role: .destructive) {
                    Task { await bridge.clean(ids: pendingIDs) }
                }
                Button("Cancel", role: .cancel) {}
            } message: {
                Text(bridge.deleteMode == "trash"
                     ? "Items will be moved to the Trash — you can restore them until you empty it."
                     : "Items will be deleted immediately. Caches rebuild on demand.")
            }
        }
        .padding()
    }

    /// B5: the exact staleness bug Task 6 already fixed in the menu bar
    /// popover ("Freed X" stuck forever after the session's first clean) —
    /// this footer had the same unconditional `if let result = bridge.lastClean`
    /// check. Applies the same freshness-gate idiom (fresh success / fresh
    /// failure / live selection readout as the fallback) so both surfaces
    /// agree, and adds the B2 error state so a failed clean never shows as a
    /// stale success here either.
    @ViewBuilder
    private var footerStatus: some View {
        TimelineView(.periodic(from: .now, by: 30)) { context in
            if bridge.isCleaning {
                HStack(spacing: 6) {
                    ProgressView().controlSize(.small)
                    Text("Cleaning…")
                }
            } else if let failure = bridge.lastCleanFailed,
                      let failedAt = bridge.lastCleanFailedAt,
                      context.date.timeIntervalSince(failedAt) < 120 {
                HStack(spacing: 6) {
                    Image(systemName: "exclamationmark.triangle.fill").foregroundStyle(.red)
                    Text(failure).foregroundStyle(.red).lineLimit(1)
                }
            } else if let result = bridge.lastClean,
                      let cleanedAt = bridge.lastCleanedAt,
                      context.date.timeIntervalSince(cleanedAt) < 120 {
                HStack(spacing: 6) {
                    Image(systemName: "checkmark.circle.fill").foregroundStyle(.green)
                    Text("Freed \(result.freed_human) (\(result.items.filter { $0.status != "skipped" }.count) items)")
                }
            } else {
                Text("\(selection.count) selected — \(ByteCountFormatter.string(fromByteCount: Int64(selectedBytes), countStyle: .file))")
                    .foregroundStyle(.secondary)
            }
        }
    }

    private func binding(for id: String) -> Binding<Bool> {
        Binding(
            get: { selection.contains(id) },
            set: { on in
                if on { selection.insert(id) } else { selection.remove(id) }
            }
        )
    }

    /// Default selection mirrors the CLI TUI: safe items checked, review items not.
    private func seedSelection() {
        selection = Set(targets.filter { $0.safe && $0.size_bytes > 0 }.map(\.id))
    }
}

struct TargetRow: View {
    let target: ScanTarget
    @Binding var isSelected: Bool
    @EnvironmentObject var bridge: CleanerBridge

    private var isCleaning: Bool { bridge.cleaningIDs.contains(target.id) }

    /// Only meaningful once the id has actually been reconciled from a real
    /// `CleanResult` (`cleanedIDs`) — never inferred from `isCleaning` going
    /// false, per the honesty rule: no "done" state before the process exits.
    private var cleanedItem: CleanItem? {
        guard bridge.cleanedIDs.contains(target.id) else { return nil }
        return bridge.lastClean?.items.first { $0.id == target.id }
    }

    var body: some View {
        HStack(spacing: 10) {
            Toggle("", isOn: $isSelected)
                .labelsHidden()
                .disabled(isCleaning)
            Circle()
                .fill(categoryColor(target.category))
                .frame(width: 7, height: 7)
            VStack(alignment: .leading, spacing: 2) {
                Text(target.label)
                    .font(.rowLabel)
                if let description = target.description, !description.isEmpty {
                    Text(description)
                        .font(.caption)
                        .foregroundStyle(.secondary)
                        .lineLimit(1)
                }
            }
            Spacer()
            if !target.safe {
                ReviewBadge()
            }
            statusSlot
        }
        .padding(.vertical, 2)
        .animation(Motion.standard, value: isCleaning)
        .animation(Motion.standard, value: cleanedItem?.status)
    }

    @ViewBuilder
    private var statusSlot: some View {
        if isCleaning {
            ProgressView()
                .controlSize(.small)
                .frame(minWidth: 70, alignment: .trailing)
        } else if let item = cleanedItem {
            HStack(spacing: 4) {
                Image(systemName: item.status == "error" ? "exclamationmark.circle.fill" : "checkmark.circle.fill")
                    .foregroundStyle(item.status == "error" ? .red : .green)
                Text(item.status == "error" ? "Failed" : ByteCountFormatter.string(fromByteCount: Int64(item.freed), countStyle: .file))
                    .monospacedDigit()
                    .foregroundStyle(.secondary)
            }
            .frame(minWidth: 70, alignment: .trailing)
            .transition(.opacity.combined(with: .scale(scale: 0.9)))
        } else {
            Text(target.size_human)
                .monospacedDigit()
                .foregroundStyle(.secondary)
                .frame(minWidth: 70, alignment: .trailing)
        }
    }
}

/// One row in the "Large Files" section — read-only, no selection/checkbox
/// (unlike `TargetRow`/`ArtifactRow`, this list isn't part of any clean
/// workflow). Styling otherwise mirrors those rows: `.rowLabel` primary
/// text, a `.caption`/`.secondary` meta line, and a trailing
/// `.monospacedDigit()` size at the same `minWidth: 70` used everywhere else
/// on the Dashboard so byte counts stay aligned across sections.
struct LargeFileRow: View {
    let entry: StorageInsightEntry

    private var relativeModified: String {
        let date = Date(timeIntervalSince1970: entry.mtime)
        let formatter = RelativeDateTimeFormatter()
        formatter.unitsStyle = .full
        return formatter.localizedString(for: date, relativeTo: Date())
    }

    var body: some View {
        HStack(spacing: 10) {
            VStack(alignment: .leading, spacing: 2) {
                Text((entry.path as NSString).lastPathComponent)
                    .font(.rowLabel)
                Text("\((entry.path as NSString).abbreviatingWithTildeInPath) · modified \(relativeModified)")
                    .font(.caption)
                    .foregroundStyle(.secondary)
                    .lineLimit(1)
                    .truncationMode(.middle)
            }
            Spacer()
            Text(entry.size_human)
                .monospacedDigit()
                .foregroundStyle(.secondary)
                .frame(minWidth: 70, alignment: .trailing)
            Button {
                NSWorkspace.shared.selectFile(entry.path, inFileViewerRootedAtPath: "")
            } label: {
                Image(systemName: "magnifyingglass")
            }
            .buttonStyle(.borderless)
            .help("Reveal in Finder")
        }
        .padding(.vertical, 2)
    }
}

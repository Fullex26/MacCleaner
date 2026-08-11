import SwiftUI

struct DashboardView: View {
    @EnvironmentObject var bridge: CleanerBridge
    @State private var selection = Set<String>()
    @State private var confirmClean = false

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

    var body: some View {
        VStack(spacing: 0) {
            header
            DiskTrendView()
                .padding(.horizontal)
                .padding(.bottom, 8)
            Divider()
            content
            Divider()
            footer
        }
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
                    Label(bridge.isBusy ? "Scanning…" : "Scan", systemImage: "arrow.clockwise")
                }
                .disabled(bridge.isBusy || bridge.isCleaning)
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
            if bridge.isBusy {
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
                            Text(group.category.capitalized)
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
            if bridge.isCleaning {
                ProgressView()
                    .controlSize(.small)
                Text("Cleaning…")
            } else if let result = bridge.lastClean {
                Image(systemName: "checkmark.circle.fill").foregroundStyle(.green)
                Text("Freed \(result.freed_human) (\(result.items.filter { $0.status != "skipped" }.count) items)")
            } else {
                Text("\(selection.count) selected — \(ByteCountFormatter.string(fromByteCount: Int64(selectedBytes), countStyle: .file))")
                    .foregroundStyle(.secondary)
            }
            Spacer()
            Button {
                confirmClean = true
            } label: {
                Label("Clean Selected", systemImage: "trash")
            }
            .buttonStyle(.borderedProminent)
            .keyboardShortcut(.defaultAction)
            .disabled(selection.isEmpty || bridge.isCleaning || bridge.isBusy)
            .confirmationDialog(
                "Delete \(selection.count) items (\(ByteCountFormatter.string(fromByteCount: Int64(selectedBytes), countStyle: .file)))?",
                isPresented: $confirmClean
            ) {
                Button(bridge.deleteMode == "trash" ? "Move to Trash" : "Delete", role: .destructive) {
                    Task { await bridge.clean(ids: Array(selection)) }
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

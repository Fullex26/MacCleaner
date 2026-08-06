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
        HStack(spacing: 16) {
            VStack(alignment: .leading, spacing: 4) {
                Text("Reclaimable: \(bridge.report?.total_reclaimable_human ?? "—")")
                    .font(.title2.bold())
                if let stats = bridge.report?.disk_stats {
                    let free = ByteCountFormatter.string(fromByteCount: Int64(stats.free_bytes), countStyle: .file)
                    let total = ByteCountFormatter.string(fromByteCount: Int64(stats.total_bytes), countStyle: .file)
                    Text("Disk: \(free) free of \(total)")
                        .font(.callout)
                        .foregroundStyle(.secondary)
                }
            }
            if let stats = bridge.report?.disk_stats {
                ProgressView(value: stats.percent_used, total: 100)
                    .frame(maxWidth: 180)
            }
            Spacer()
            Button {
                Task { await bridge.scan() }
            } label: {
                Label(bridge.isBusy ? "Scanning…" : "Scan", systemImage: "arrow.clockwise")
            }
            .disabled(bridge.isBusy || bridge.isCleaning)
        }
        .padding()
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

    var body: some View {
        HStack(spacing: 10) {
            Toggle("", isOn: $isSelected)
                .labelsHidden()
            VStack(alignment: .leading, spacing: 2) {
                Text(target.label)
                if let description = target.description, !description.isEmpty {
                    Text(description)
                        .font(.caption)
                        .foregroundStyle(.secondary)
                        .lineLimit(1)
                }
            }
            Spacer()
            if !target.safe {
                Text("REVIEW")
                    .font(.caption2.bold())
                    .padding(.horizontal, 6)
                    .padding(.vertical, 2)
                    .background(.yellow.opacity(0.25), in: Capsule())
                    .foregroundStyle(.orange)
            }
            Text(target.size_human)
                .monospacedDigit()
                .foregroundStyle(.secondary)
                .frame(minWidth: 70, alignment: .trailing)
        }
        .padding(.vertical, 2)
    }
}

import SwiftUI

struct HistoryView: View {
    @EnvironmentObject var bridge: CleanerBridge

    var body: some View {
        Group {
            if bridge.history.isEmpty {
                VStack {
                    Spacer()
                    Label("No cleanups yet.", systemImage: "clock")
                        .font(.metaCaption)
                        .foregroundStyle(.secondary)
                    Spacer()
                }
            } else {
                ScrollView {
                    LazyVStack(spacing: 10) {
                        ForEach(bridge.history) { run in
                            HistoryCard(run: run)
                        }
                    }
                    .padding()
                }
            }
        }
        .task { await bridge.loadHistory() }
    }
}

/// Preserve recorded bytes even when an item partially failed.
struct HistoryCard: View {
    let run: HistoryRun
    @State private var expanded = false

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack(alignment: .top, spacing: 16) {
                VStack(alignment: .leading, spacing: 3) {
                    Text(formatTimestamp(run.timestamp)).font(.rowLabel)
                    Text(relativeTimeText).font(.metaCaption).foregroundStyle(.secondary)
                    Text(run.disk_after).font(.metaCaption).foregroundStyle(.secondary)
                }
                Spacer()
                VStack(alignment: .trailing, spacing: 3) {
                    Text(run.total_freed_human)
                        .font(.system(size: 20, weight: .bold, design: .rounded))
                        .monospacedDigit()
                        .foregroundStyle(run.total_freed_bytes > 0 ? .green : .secondary)
                    Text("Reported freed").font(.metaCaption).foregroundStyle(.secondary)
                }
            }
            if run.items.isEmpty {
                Text(run.outcomeSummary).font(.metaCaption).foregroundStyle(.secondary)
            } else {
                DisclosureGroup(isExpanded: $expanded) {
                    VStack(alignment: .leading, spacing: 10) {
                        // History may contain repeated IDs; retain every recorded outcome.
                        ForEach(Array(run.items.enumerated()), id: \.offset) { _, item in
                            VStack(alignment: .leading, spacing: 3) {
                                Text(item.label).font(.rowLabel)
                                Text(item.outcomeLabel)
                                    .foregroundStyle(item.status == "error" ? .red : .secondary)
                                Text(item.freedDetail)
                                    .font(.metaCaption).foregroundStyle(.secondary)
                                if let error = item.error {
                                    Text(error).font(.metaCaption).foregroundStyle(.red).textSelection(.enabled)
                                }
                            }
                            .frame(maxWidth: .infinity, alignment: .leading)
                        }
                    }
                    .padding(.top, 8)
                } label: {
                    Text(run.outcomeSummary).font(.metaCaption)
                }
            }
        }
        .padding()
        .glassPanel()
    }

    private var relativeTimeText: String {
        guard let date = CleanerBridge.parseTimestamp(run.timestamp) else { return "—" }
        let formatter = RelativeDateTimeFormatter()
        formatter.unitsStyle = .full
        return formatter.localizedString(for: date, relativeTo: Date())
    }

    private func formatTimestamp(_ iso: String) -> String {
        // Engine timestamps are ISO-8601 without timezone, e.g. 2026-07-14T09:30:00.123456
        String(iso.prefix(16)).replacingOccurrences(of: "T", with: "  ")
    }
}

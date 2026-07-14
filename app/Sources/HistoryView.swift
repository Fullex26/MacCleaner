import SwiftUI

struct HistoryView: View {
    @EnvironmentObject var bridge: CleanerBridge

    var body: some View {
        Group {
            if bridge.history.isEmpty {
                VStack {
                    Spacer()
                    Label("No cleanups yet.", systemImage: "clock")
                        .foregroundStyle(.secondary)
                    Spacer()
                }
            } else {
                List(bridge.history) { run in
                    HStack {
                        VStack(alignment: .leading, spacing: 2) {
                            Text(formatTimestamp(run.timestamp))
                            Text(run.disk_after)
                                .font(.caption)
                                .foregroundStyle(.secondary)
                        }
                        Spacer()
                        Text(run.total_freed_human)
                            .monospacedDigit()
                            .foregroundStyle(run.total_freed_bytes > 0 ? .green : .secondary)
                    }
                    .padding(.vertical, 2)
                }
                .listStyle(.inset)
            }
        }
        .task { await bridge.loadHistory() }
    }

    private func formatTimestamp(_ iso: String) -> String {
        // Engine timestamps are ISO-8601 without timezone, e.g. 2026-07-14T09:30:00.123456
        String(iso.prefix(16)).replacingOccurrences(of: "T", with: "  ")
    }
}

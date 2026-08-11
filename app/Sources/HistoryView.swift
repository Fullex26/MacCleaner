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

/// One cleanup run as a glass card: date + relative-time caption on the
/// leading side, freed amount (scaled-down hero figure) + item count on the
/// trailing side.
struct HistoryCard: View {
    let run: HistoryRun

    /// Mirrors the Dashboard footer's count: "skipped" review targets that
    /// were merely offered, not actually removed, don't count as freed items.
    private var itemCount: Int {
        run.items.filter { $0.status != "skipped" }.count
    }

    var body: some View {
        HStack(alignment: .top, spacing: 16) {
            VStack(alignment: .leading, spacing: 3) {
                Text(formatTimestamp(run.timestamp))
                    .font(.rowLabel)
                Text(relativeTimeText)
                    .font(.metaCaption)
                    .foregroundStyle(.secondary)
                Text(run.disk_after)
                    .font(.metaCaption)
                    .foregroundStyle(.secondary)
            }
            Spacer()
            VStack(alignment: .trailing, spacing: 3) {
                Text(run.total_freed_human)
                    .font(.system(size: 20, weight: .bold, design: .rounded))
                    .monospacedDigit()
                    .foregroundStyle(run.total_freed_bytes > 0 ? .green : .secondary)
                Text("\(itemCount) item\(itemCount == 1 ? "" : "s")")
                    .font(.metaCaption)
                    .foregroundStyle(.secondary)
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

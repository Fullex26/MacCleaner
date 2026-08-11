import SwiftUI
import Charts

/// Free-space-over-time from the engine's daily snapshots, with the low-disk
/// threshold drawn as a rule line. Pure rendering: all data comes from
/// `report --json` via the bridge's light tick.
struct DiskTrendView: View {
    @EnvironmentObject var bridge: CleanerBridge

    private struct Point: Identifiable {
        let day: Date
        let freeGB: Double
        var id: Date { day }
    }

    private var points: [Point] {
        // Skip any snapshot missing the fields this chart needs (see
        // DiskSnapshot's Codable comment) instead of failing to render at
        // all — one corrupted entry shouldn't take out the whole trend.
        bridge.diskSnapshots.compactMap { snap in
            guard let ts = snap.ts, let day = CleanerBridge.parseTimestamp(ts),
                  let freeBytes = snap.disk_free_bytes else { return nil }
            return Point(day: day, freeGB: Double(freeBytes) / 1_073_741_824)
        }
    }

    private var thresholdGB: Double { bridge.lowDiskThresholdGB }

    var body: some View {
        Group {
            if points.count >= 2 {
                Chart {
                    ForEach(points) { p in
                        LineMark(x: .value("Day", p.day),
                                 y: .value("Free (GB)", p.freeGB))
                            .foregroundStyle(Color.accentCyan)
                    }
                    RuleMark(y: .value("Low-disk warning", thresholdGB))
                        .lineStyle(StrokeStyle(lineWidth: 1, dash: [4, 4]))
                        .foregroundStyle(.orange)
                        .annotation(position: .top, alignment: .trailing) {
                            Text("Low-disk warning")
                                .font(.caption2)
                                .foregroundStyle(.orange)
                        }
                }
                .chartXAxis {
                    AxisMarks { _ in
                        AxisGridLine().foregroundStyle(.secondary)
                        AxisValueLabel()
                    }
                }
                .chartYAxis {
                    AxisMarks { _ in
                        AxisGridLine().foregroundStyle(.secondary)
                        AxisValueLabel()
                    }
                }
                .chartYScale(domain: 0...maxY)
                .chartYAxisLabel("Free (GB)")
                .frame(height: 140)
                .padding()
            } else {
                // Compact rather than reserving the full 140pt chart height
                // for one centered sentence — a fresh install shouldn't
                // permanently lose that much target-list height.
                Text("Disk trends appear after a couple of days of scans.")
                    .font(.callout)
                    .foregroundStyle(.secondary)
                    .frame(maxWidth: .infinity, alignment: .center)
                    .frame(height: 32)
                    .padding()
            }
        }
        .glassPanel()
    }

    private var maxY: Double {
        let peak = points.map(\.freeGB).max() ?? thresholdGB
        // Floor the domain so a zero threshold with all-zero free space
        // (or any other degenerate case) never yields chartYScale(0...0).
        return max(peak, thresholdGB * 1.2, 1)
    }
}

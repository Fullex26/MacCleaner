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
        bridge.diskSnapshots.compactMap { snap in
            guard let day = CleanerBridge.parseTimestamp(snap.ts) else { return nil }
            return Point(day: day, freeGB: Double(snap.disk_free_bytes) / 1_073_741_824)
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
                .chartYScale(domain: 0...maxY)
                .chartYAxisLabel("Free (GB)")
            } else {
                Text("Disk trends appear after a couple of days of scans.")
                    .font(.callout)
                    .foregroundStyle(.secondary)
                    .frame(maxWidth: .infinity, alignment: .center)
            }
        }
        .frame(height: 140)
    }

    private var maxY: Double {
        let peak = points.map(\.freeGB).max() ?? thresholdGB
        return max(peak, thresholdGB * 1.2)
    }
}

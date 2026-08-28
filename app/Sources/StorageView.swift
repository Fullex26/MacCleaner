import SwiftUI
import AppKit

/// The storage browser: a read-only drill-down over the whole disk, with the
/// Trash as the only removal path.
///
/// This is deliberately not part of the Dashboard's cleanup flow. The cleanup
/// engine is scoped to rebuildable caches inside `$HOME` and hard-deletes what
/// it takes — correct for a build cache, wrong for a folder of photos. So this
/// view can go anywhere on the disk (including system locations, read-only),
/// never marks anything "safe to auto-clean", and removes only via
/// `FileManager.trashItem`, which is recoverable and lets macOS's own
/// permissions decide what is off limits.
struct StorageView: View {
    @EnvironmentObject var bridge: CleanerBridge

    /// Visited levels, oldest first — the breadcrumb and the Back button both
    /// read from this rather than re-deriving parents from the path string,
    /// so going back always lands exactly where the user came from.
    @State private var trail: [String] = []
    @State private var pendingTrash: StorageMapEntry?

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            header
            Divider()
            content
        }
        // Pin the pane's bounds explicitly. Without this the VStack takes its
        // ideal size from the List's content, so one over-wide row used to
        // stretch the whole view past the detail pane and push the header off
        // the top of the window entirely.
        .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
        .clipped()
        .task {
            if bridge.storageMap == nil && !bridge.isScanningStorageMap {
                await bridge.scanStorageMap(nil)
            }
        }
        .confirmationDialog(
            pendingTrash.map { "Move “\($0.name)” to the Trash?" } ?? "",
            isPresented: Binding(get: { pendingTrash != nil },
                                 set: { if !$0 { pendingTrash = nil } }),
            titleVisibility: .visible
        ) {
            Button("Move to Trash", role: .destructive) {
                if let entry = pendingTrash {
                    pendingTrash = nil
                    Task {
                        if await bridge.moveToTrash(entry.path) {
                            await bridge.scanStorageMap(bridge.storageMap?.root)
                        }
                    }
                }
            }
            Button("Cancel", role: .cancel) { pendingTrash = nil }
        } message: {
            if let entry = pendingTrash {
                Text("\(entry.size_human) · You can put it back from the Trash.")
            }
        }
    }

    // MARK: - Header

    private var header: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack(alignment: .firstTextBaseline) {
                Text("Storage")
                    .font(.title2.weight(.semibold))
                Spacer()
                if let map = bridge.storageMap {
                    Text(map.total_human)
                        .font(.title3.monospacedDigit().weight(.medium))
                        .foregroundStyle(.secondary)
                }
                if bridge.isScanningStorageMap {
                    ProgressView().scaleEffect(0.5).frame(width: 16, height: 16)
                }
            }

            Text("Everything on this Mac, largest first. Read-only — the only way to remove something here is the Trash, so it can always be put back.")
                .font(.caption)
                .foregroundStyle(.secondary)
                .fixedSize(horizontal: false, vertical: true)

            HStack(spacing: 8) {
                Button {
                    goBack()
                } label: {
                    Label("Back", systemImage: "chevron.left")
                }
                .disabled(trail.isEmpty || bridge.isScanningStorageMap)

                Button {
                    Task {
                        trail = []
                        await bridge.scanStorageMap(nil)
                    }
                } label: {
                    Label("Home", systemImage: "house")
                }
                .disabled(bridge.isScanningStorageMap)

                Menu {
                    Button("Whole disk (/)") { jump("/") }
                    Button("Applications") { jump("/Applications") }
                    Button("System Library") { jump("/Library") }
                    Button("Your Library") { jump(NSHomeDirectory() + "/Library") }
                    Button("Documents") { jump(NSHomeDirectory() + "/Documents") }
                } label: {
                    Label("Go to", systemImage: "arrow.right.circle")
                }
                .menuStyle(.borderlessButton)
                .fixedSize()
                .disabled(bridge.isScanningStorageMap)

                Spacer()

                if let map = bridge.storageMap {
                    Button {
                        NSWorkspace.shared.selectFile(nil, inFileViewerRootedAtPath: map.root)
                    } label: {
                        Label("Open in Finder", systemImage: "folder")
                    }
                }
            }
            .font(.callout)

            if let map = bridge.storageMap {
                Text(map.root)
                    .font(.system(.caption, design: .monospaced))
                    .foregroundStyle(.secondary)
                    .textSelection(.enabled)
                    .lineLimit(1)
                    .truncationMode(.head)
            }
        }
        .padding()
    }

    // MARK: - Content

    @ViewBuilder
    private var content: some View {
        if let error = bridge.storageMapError, bridge.storageMap == nil {
            message("Couldn't read that location.", detail: error, symbol: "exclamationmark.triangle")
        } else if bridge.storageMap == nil {
            message("Measuring…",
                    detail: "First scan of a large folder can take a moment.",
                    symbol: "clock")
        } else if let map = bridge.storageMap, map.children.isEmpty {
            message("Nothing in here.",
                    detail: bridge.storageMapError ?? "This folder is empty, or its contents aren't readable without administrator access.",
                    symbol: "tray")
        } else if let map = bridge.storageMap {
            List {
                if let error = bridge.storageMapError {
                    Label(error, systemImage: "exclamationmark.triangle")
                        .font(.caption)
                        .foregroundStyle(.orange)
                }
                ForEach(map.children) { entry in
                    StorageRow(
                        entry: entry,
                        fraction: map.total_bytes > 0
                            ? Double(entry.size_bytes) / Double(map.total_bytes) : 0,
                        onOpen: entry.isDirectory ? { drill(into: entry) } : nil,
                        onReveal: { NSWorkspace.shared.selectFile(entry.path, inFileViewerRootedAtPath: "") },
                        onTrash: { pendingTrash = entry }
                    )
                }
            }
            .listStyle(.inset)
            .frame(maxWidth: .infinity, maxHeight: .infinity)
        }
    }

    private func message(_ title: String, detail: String, symbol: String) -> some View {
        VStack(spacing: 6) {
            Image(systemName: symbol).font(.title2).foregroundStyle(.secondary)
            Text(title).font(.callout.weight(.medium))
            Text(detail)
                .font(.caption)
                .foregroundStyle(.secondary)
                .multilineTextAlignment(.center)
                .fixedSize(horizontal: false, vertical: true)
        }
        .frame(maxWidth: 420)
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .padding()
    }

    // MARK: - Navigation

    private func drill(into entry: StorageMapEntry) {
        guard let current = bridge.storageMap?.root else { return }
        trail.append(current)
        Task { await bridge.scanStorageMap(entry.path) }
    }

    private func goBack() {
        guard let previous = trail.popLast() else { return }
        Task { await bridge.scanStorageMap(previous) }
    }

    private func jump(_ path: String) {
        if let current = bridge.storageMap?.root { trail.append(current) }
        Task { await bridge.scanStorageMap(path) }
    }
}

// MARK: - Row

private struct StorageRow: View {
    let entry: StorageMapEntry
    let fraction: Double
    let onOpen: (() -> Void)?
    let onReveal: () -> Void
    let onTrash: () -> Void

    @State private var hovering = false

    /// Fixed so the bar can never claim row width from the columns beside it.
    private static let barWidth: CGFloat = 120

    /// `fraction` comes from engine sizes that can round above the parent
    /// total (or be NaN if a level reports 0 bytes), which would draw a bar
    /// past its track.
    private var clampedFraction: CGFloat {
        let f = CGFloat(fraction)
        return f.isFinite ? min(max(f, 0), 1) : 0
    }

    var body: some View {
        HStack(spacing: 12) {
            Image(systemName: symbol)
                .foregroundStyle(Color.forCategory(entry.category))
                .frame(width: 18)

            Text(entry.name)
                .lineLimit(1)
                .truncationMode(.middle)
                .frame(maxWidth: .infinity, alignment: .leading)

            // Proportional bar on a FIXED-WIDTH track. This used to be a
            // GeometryReader, which is greedy in both axes: it claimed the
            // whole row, shoving the category, size and buttons hard against
            // the window edge and blowing the layout out sideways. A fixed
            // track needs no measurement and cannot expand.
            ZStack(alignment: .leading) {
                Capsule()
                    .fill(Color.secondary.opacity(0.15))
                Capsule()
                    .fill(Color.forCategory(entry.category).opacity(0.85))
                    .frame(width: max(2, Self.barWidth * clampedFraction))
            }
            .frame(width: Self.barWidth, height: 4)

            Text(entry.category)
                .font(.caption2)
                .foregroundStyle(.secondary)
                .frame(width: 82, alignment: .leading)

            Text(entry.size_human)
                .font(.callout.monospacedDigit())
                .frame(width: 78, alignment: .trailing)

            HStack(spacing: 2) {
                Button(action: onReveal) {
                    Image(systemName: "magnifyingglass")
                }
                .buttonStyle(.borderless)
                .help("Reveal in Finder")

                Button(action: onTrash) {
                    Image(systemName: "trash")
                }
                .buttonStyle(.borderless)
                .help("Move to Trash")

                if let onOpen {
                    Button(action: onOpen) {
                        Image(systemName: "chevron.right")
                    }
                    .buttonStyle(.borderless)
                    .help("Look inside")
                } else {
                    Image(systemName: "chevron.right").opacity(0)
                }
            }
            .opacity(hovering ? 1 : 0.35)
        }
        .padding(.vertical, 3)
        .contentShape(Rectangle())
        .onHover { hovering = $0 }
        .onTapGesture(count: 2) { onOpen?() }
    }

    private var symbol: String {
        switch entry.kind {
        case "dir":  return "folder.fill"
        case "link": return "link"
        default:     return "doc.fill"
        }
    }
}

private extension Color {
    /// Mirrors the storage-map categories the engine assigns. Kept local to
    /// this view: these are browser buckets, not the cleanup categories that
    /// `DesignSystem`'s palette covers.
    static func forCategory(_ category: String) -> Color {
        switch category {
        case "applications": return .blue
        case "documents":    return .orange
        case "media":        return .pink
        case "developer":    return .teal
        case "caches":       return .green
        case "appdata":      return .purple
        case "system":       return .gray
        default:             return .secondary
        }
    }
}

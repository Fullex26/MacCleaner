import SwiftUI

struct CleanupPreviewSheet: View {
    @EnvironmentObject var bridge: CleanerBridge
    @Environment(\.dismiss) private var dismiss
    let ids: [String]
    @State private var reviewed: ReviewedCleanup?
    @State private var error: String?
    @State private var loading = false

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            Text("Review cleanup").font(.headline)
            Text("Paths are resolved again at execution using the current safety rules. Sizes are estimates; overlapping paths may count the same bytes.")
                .font(.metaCaption).foregroundStyle(.secondary).lineLimit(3)
            ScrollView {
                VStack(alignment: .leading, spacing: 14) {
                    if loading { ProgressView("Resolving selected targets…") }
                    if let error { Text(error).foregroundStyle(.red).textSelection(.enabled) }
                    if let reviewed {
                        if let problem = reviewed.problem {
                            Text("Preview incomplete: \(problem)").foregroundStyle(.red).textSelection(.enabled)
                        }
                        Text(reviewed.mode == "trash"
                             ? "Paths move to Trash, except existing Trash contents are permanently deleted. Commands run their own cleanup and cannot be restored from Trash."
                             : "Paths are deleted immediately. Commands run their own cleanup.")
                            .font(.metaCaption)
                        ForEach(reviewed.preview.items) { item in
                            VStack(alignment: .leading, spacing: 5) {
                                Text(item.label).font(.headline)
                                Text(item.actionDescription(mode: reviewed.mode))
                                    .font(.metaCaption).foregroundStyle(.orange)
                                if let command = item.cmd {
                                    Text("Command").font(.metaCaption).foregroundStyle(.secondary)
                                    Text(command).font(.system(.body, design: .monospaced)).textSelection(.enabled)
                                }
                                ForEach(Array(item.paths.enumerated()), id: \.offset) { _, entry in
                                    Text(entry.path).textSelection(.enabled)
                                    Text(ByteCountFormatter.string(fromByteCount: Int64(entry.size_bytes), countStyle: .file))
                                        .font(.metaCaption).foregroundStyle(.secondary)
                                }
                                if let diagnostic = item.preview_error {
                                    Text(diagnostic).foregroundStyle(.red).textSelection(.enabled)
                                }
                            }
                            Divider()
                        }
                    }
                }
                .frame(maxWidth: .infinity, alignment: .leading)
            }
            HStack {
                Button("Cancel", role: .cancel) { dismiss() }
                Button("Refresh preview") { Task { await refresh() } }.disabled(loading)
                Spacer()
                Button("Confirm Cleanup", role: .destructive) {
                    guard let reviewed else { return }
                    Task {
                        do {
                            try await bridge.clean(reviewed: reviewed)
                            dismiss()
                        } catch {
                            self.error = error.localizedDescription
                            self.reviewed = nil
                        }
                    }
                }
                .disabled(loading || reviewed == nil || reviewed?.problem != nil || bridge.isCleaning)
            }
        }
        .padding(20)
        .frame(width: 620, height: 480)
        .task { await refresh() }
    }

    private func refresh() async {
        loading = true
        reviewed = nil
        error = nil
        defer { loading = false }
        do { reviewed = try await bridge.previewCleanup(ids: ids) }
        catch { self.error = error.localizedDescription }
    }
}

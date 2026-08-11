import SwiftUI

struct ProjectsView: View {
    @EnvironmentObject var bridge: CleanerBridge
    @State private var selection = Set<String>()
    @State private var confirmClean = false

    private var artifacts: [ProjectArtifact] {
        bridge.projects?.artifacts ?? []
    }

    private var selectedBytes: Int {
        artifacts.filter { selection.contains($0.id) }.reduce(0) { $0 + $1.size_bytes }
    }

    var body: some View {
        VStack(spacing: 0) {
            header
            Divider()
            content
            Divider()
            footer
        }
    }

    private var header: some View {
        HStack {
            VStack(alignment: .leading, spacing: 4) {
                Text("Stale project artifacts")
                    .font(.title2.bold())
                if let projects = bridge.projects {
                    Text("node_modules, .venv, target… untouched for \(projects.min_age_days)+ days under \(projects.roots.map { ($0 as NSString).abbreviatingWithTildeInPath }.joined(separator: ", "))")
                        .font(.callout)
                        .foregroundStyle(.secondary)
                        .lineLimit(2)
                } else {
                    Text("Finds old build artifacts you can safely regenerate (npm install, cargo build…)")
                        .font(.callout)
                        .foregroundStyle(.secondary)
                }
            }
            Spacer()
            Button {
                Task { await bridge.scanProjects() }
            } label: {
                Label(bridge.isBusy ? "Scanning…" : "Scan Projects", systemImage: "arrow.clockwise")
            }
            .disabled(bridge.isBusy || bridge.isCleaning)
        }
        .padding()
    }

    @ViewBuilder
    private var content: some View {
        if bridge.projects == nil {
            Spacer()
            if bridge.isBusy {
                ProgressView("Scanning project folders…")
            } else {
                Text("Press Scan Projects to look for stale build artifacts.")
                    .foregroundStyle(.secondary)
            }
            Spacer()
        } else if artifacts.isEmpty {
            Spacer()
            Label("No stale artifacts found — nice and tidy.", systemImage: "checkmark.seal")
                .foregroundStyle(.secondary)
            Spacer()
        } else {
            bulkBar
            Divider()
            List(artifacts) { artifact in
                ArtifactRow(artifact: artifact, isSelected: binding(for: artifact.id))
            }
            .listStyle(.inset)
        }
    }

    /// Bulk-select bar above the artifact list: All / None, plus a running
    /// "N of M" count. Styled identically to Dashboard's bulk bar, minus
    /// "Safe only" — project artifacts have no safe/review distinction.
    private var bulkBar: some View {
        HStack(spacing: 12) {
            Text("Select:").font(.metaCaption).foregroundStyle(.secondary)
            Button("All")  { selection = Set(artifacts.map(\.id)) }
            Button("None") { selection.removeAll() }
            Spacer()
            Text("\(selection.count) of \(artifacts.count)")
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
            } else {
                Text("\(selection.count) selected — \(ByteCountFormatter.string(fromByteCount: Int64(selectedBytes), countStyle: .file))")
                    .foregroundStyle(.secondary)
            }
            Spacer()
            Button {
                confirmClean = true
            } label: {
                Label("Remove Selected", systemImage: "trash")
            }
            .buttonStyle(.borderedProminent)
            .keyboardShortcut(.defaultAction)
            .disabled(selection.isEmpty || bridge.isCleaning || bridge.isBusy)
            .confirmationDialog(
                "Remove \(selection.count) artifacts (\(ByteCountFormatter.string(fromByteCount: Int64(selectedBytes), countStyle: .file)))?",
                isPresented: $confirmClean
            ) {
                Button(bridge.deleteMode == "trash" ? "Move to Trash" : "Delete", role: .destructive) {
                    let ids = Array(selection)
                    selection.removeAll()
                    Task { await bridge.cleanProjects(ids: ids) }
                }
                Button("Cancel", role: .cancel) {}
            } message: {
                Text("Rebuild them any time with npm install, uv sync, cargo build, pod install…")
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
}

/// Maps an artifact's `kind` (a raw directory name like `node_modules` or
/// `.venv`) to the palette key `categoryColor` recognizes, so the dot reads
/// as "this is a Node artifact" rather than a color keyed to the literal
/// folder name. Anything not called out explicitly (`.nuxt`, `.turbo`,
/// `.pytest_cache`, a future manifest kind, …) falls through to
/// `categoryColor`'s own hash-derived fallback, keyed on the raw kind string
/// so it's still stable and distinct across launches.
private func artifactDotColor(for kind: String) -> Color {
    switch kind {
    case "node_modules", ".next", "dist", "build":
        return categoryColor("node")
    case ".venv", "venv":
        return categoryColor("python")
    case "target":
        return categoryColor("rust")
    case "Pods":
        return categoryColor("cocoapods")
    default:
        return categoryColor(kind)
    }
}

struct ArtifactRow: View {
    let artifact: ProjectArtifact
    @Binding var isSelected: Bool

    var body: some View {
        HStack(spacing: 10) {
            Toggle("", isOn: $isSelected)
                .labelsHidden()
            Circle()
                .fill(artifactDotColor(for: artifact.kind))
                .frame(width: 7, height: 7)
            VStack(alignment: .leading, spacing: 2) {
                Text("\(artifact.kind) — \((artifact.project as NSString).abbreviatingWithTildeInPath)")
                    .font(.rowLabel)
                Text("Untouched for \(artifact.age_days) days")
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }
            Spacer()
            gitChips
            Text(ByteCountFormatter.string(fromByteCount: Int64(artifact.size_bytes), countStyle: .file))
                .monospacedDigit()
                .foregroundStyle(.secondary)
                .frame(minWidth: 70, alignment: .trailing)
        }
        .padding(.vertical, 2)
    }

    /// Outline-amber chips for uncommitted/unpushed work — the same
    /// `ReviewBadge` capsule the Dashboard uses for review-only targets,
    /// just with different text, so it's never a second hand-rolled capsule.
    @ViewBuilder
    private var gitChips: some View {
        if let git = artifact.git {
            HStack(spacing: 4) {
                if git.dirty {
                    ReviewBadge(text: "DIRTY")
                }
                if git.unpushed {
                    ReviewBadge(text: "UNPUSHED")
                }
            }
        }
    }
}

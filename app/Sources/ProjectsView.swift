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
            List(artifacts) { artifact in
                HStack(spacing: 10) {
                    Toggle("", isOn: binding(for: artifact.id))
                        .labelsHidden()
                    VStack(alignment: .leading, spacing: 2) {
                        Text("\(artifact.kind) — \((artifact.project as NSString).abbreviatingWithTildeInPath)")
                        Text("Untouched for \(artifact.age_days) days")
                            .font(.caption)
                            .foregroundStyle(.secondary)
                    }
                    Spacer()
                    Text(ByteCountFormatter.string(fromByteCount: Int64(artifact.size_bytes), countStyle: .file))
                        .monospacedDigit()
                        .foregroundStyle(.secondary)
                }
                .padding(.vertical, 2)
            }
            .listStyle(.inset)
        }
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

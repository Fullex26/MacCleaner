import SwiftUI

struct SettingsView: View {
    @EnvironmentObject var bridge: CleanerBridge

    var body: some View {
        Form {
            Section {
                Picker("When cleaning", selection: Binding(
                    get: { bridge.deleteMode },
                    set: { mode in Task { await bridge.setDeleteMode(mode) } }
                )) {
                    Text("Delete immediately").tag("rm")
                    Text("Move to Trash (recoverable)").tag("trash")
                }
                .pickerStyle(.radioGroup)
            } header: {
                Text("Deletion mode")
            }

            Section {
                if bridge.categories.isEmpty {
                    Text("Loading categories…").foregroundStyle(.secondary)
                } else {
                    ForEach(bridge.categories) { category in
                        Toggle(isOn: Binding(
                            get: { category.enabled },
                            set: { on in Task { await bridge.setCategory(category.name, enabled: on) } }
                        )) {
                            VStack(alignment: .leading, spacing: 2) {
                                Text(category.name.capitalized)
                                Text(category.description)
                                    .font(.caption)
                                    .foregroundStyle(.secondary)
                            }
                        }
                    }
                }
            } header: {
                Text("Categories")
            } footer: {
                Text("Settings are shared with the CLI (config.json next to cleaner.py).")
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }

            Section {
                LabeledContent("Engine", value: (CleanerBridge.enginePath() as NSString).abbreviatingWithTildeInPath)
                LabeledContent("Interface docs", value: "AGENTS.md in the repo")
            } header: {
                Text("About")
            }
        }
        .formStyle(.grouped)
        .task { await bridge.loadSettings() }
    }
}

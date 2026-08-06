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
                if bridge.scheduleSupported {
                    Picker("Automatic cleanup", selection: Binding(
                        get: { bridge.scheduleStatus?.schedule ?? "off" },
                        set: { choice in Task { await bridge.setSchedule(choice) } }
                    )) {
                        Text("Off").tag("off")
                        Text("Weekly — Mondays at 9am").tag("weekly")
                        Text("Monthly — 1st at 9am").tag("monthly")
                    }
                    .pickerStyle(.radioGroup)

                    Text(scheduleCaption)
                        .font(.caption)
                        .foregroundStyle(.secondary)
                } else {
                    Text("Update the MacCleaner CLI to manage scheduling here.")
                        .foregroundStyle(.secondary)
                }
            } header: {
                Text("Schedule")
            }

            Section {
                Toggle(isOn: Binding(
                    get: { bridge.notificationsEnabled },
                    set: { on in Task { await bridge.setNotifications(on) } }
                )) {
                    VStack(alignment: .leading, spacing: 2) {
                        Text("Notify when a clean finishes")
                        Text("Includes scheduled cleans run in the background")
                            .font(.caption)
                            .foregroundStyle(.secondary)
                    }
                }

                Toggle(isOn: Binding(
                    get: { bridge.lowDiskAlertsEnabled },
                    set: { on in Task { await bridge.setLowDiskAlerts(on) } }
                )) {
                    Text("Warn when disk space is low")
                }

                if bridge.lowDiskAlertsEnabled {
                    LabeledContent("Warn below") {
                        Stepper("\(Int(bridge.lowDiskThresholdGB)) GB",
                                value: Binding(
                                    get: { bridge.lowDiskThresholdGB },
                                    set: { gb in Task { await bridge.setLowDiskThreshold(gb) } }
                                ),
                                in: 1...500, step: 1)
                    }
                }
            } header: {
                Text("Notifications")
            }

            Section {
                LabeledContent("Engine", value: (CleanerBridge.enginePath() as NSString).abbreviatingWithTildeInPath)
                LabeledContent("Interface docs", value: "AGENTS.md in the repo")
            } header: {
                Text("About")
            }
        }
        .formStyle(.grouped)
        .task { await bridge.loadSettings(); await bridge.loadSchedule() }
    }

    private var scheduleCaption: String {
        guard let status = bridge.scheduleStatus else { return "" }
        if status.legacy_cron {
            return "A legacy cron schedule exists — choosing an option migrates it to launchd."
        }
        if status.schedule != nil {
            let allLoaded = status.agents.allSatisfy(\.loaded)
            return allLoaded
                ? "Active — cleans run in the background and notify when done. Low-disk check: hourly."
                : "Installed but not loaded — pick the schedule again to reload it."
        }
        return "No automatic cleanup. Scans and cleans only run when you start them."
    }
}

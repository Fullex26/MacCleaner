import SwiftUI

struct SettingsView: View {
    @EnvironmentObject var bridge: CleanerBridge
    @ObservedObject private var updater = UpdaterManager.shared

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 20) {
                generalSection
                appearanceSection
                scheduleSection
                alertsSection
                v3Section
                updatesSection
            }
            .padding()
            .frame(maxWidth: .infinity, alignment: .leading)
        }
        .task { await bridge.loadSettings(); await bridge.loadSchedule() }
    }

    // MARK: - General (deletion mode + categories + about)

    private var generalSection: some View {
        SettingsSection(title: "General") {
            Picker("When cleaning", selection: Binding(
                get: { bridge.deleteMode },
                set: { mode in Task { await bridge.setDeleteMode(mode) } }
            )) {
                Text("Delete immediately").tag("rm")
                Text("Move to Trash (recoverable)").tag("trash")
            }
            .pickerStyle(.radioGroup)

            Divider()

            VStack(alignment: .leading, spacing: 10) {
                Text("Categories")
                    .font(.rowLabel)

                if bridge.categories.isEmpty {
                    Text("Loading categories…").foregroundStyle(.secondary)
                } else {
                    ForEach(bridge.categories) { category in
                        Toggle(isOn: Binding(
                            get: { category.enabled },
                            set: { on in Task { await bridge.setCategory(category.name, enabled: on) } }
                        )) {
                            VStack(alignment: .leading, spacing: 2) {
                                Text(categoryDisplayName(category.name))
                                Text(category.description)
                                    .font(.caption)
                                    .foregroundStyle(.secondary)
                            }
                        }
                    }
                }

                Text("Settings are shared with the CLI (config.json next to cleaner.py).")
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }

            Divider()

            VStack(alignment: .leading, spacing: 4) {
                LabeledContent("Engine", value: (CleanerBridge.enginePath() as NSString).abbreviatingWithTildeInPath)
                LabeledContent("Interface docs", value: "AGENTS.md in the repo")
            }
        }
    }

    // MARK: - Schedule

    private var scheduleSection: some View {
        SettingsSection(title: "Schedule") {
            if bridge.scheduleSupported {
                Picker("Automatic cleanup", selection: Binding(
                    get: { bridge.isSchedulingBusy
                        ? (bridge.pendingSchedule ?? "off")
                        : (bridge.scheduleStatus?.schedule ?? "off") },
                    set: { choice in Task { await bridge.setSchedule(choice) } }
                )) {
                    Text("Off").tag("off")
                    Text("Weekly — Mondays at 9am").tag("weekly")
                    Text("Monthly — 1st at 9am").tag("monthly")
                }
                .pickerStyle(.radioGroup)
                .disabled(bridge.isSchedulingBusy)

                Text(scheduleCaption)
                    .font(.caption)
                    .foregroundStyle(.secondary)

                // The schedule was once reported broken while running
                // perfectly — because it was invisible. Next-run time, the
                // last clean on record, and a way to fire one now make it
                // observable instead of an act of faith.
                if bridge.scheduleStatus?.schedule != nil {
                    if let next = nextRunText {
                        LabeledContent("Next clean") { Text(next) }
                            .font(.callout)
                    }
                    if let last = bridge.lastCleanedAt {
                        LabeledContent("Last clean") {
                            Text(last, format: .relative(presentation: .named))
                        }
                        .font(.callout)
                    }
                    HStack(spacing: 8) {
                        Button {
                            Task { await bridge.runScheduleNow() }
                        } label: {
                            Label(bridge.isRunningScheduleNow ? "Starting…" : "Run Now",
                                  systemImage: "play.circle")
                        }
                        .disabled(bridge.isRunningScheduleNow || bridge.isSchedulingBusy)
                        if let feedback = bridge.scheduleRunFeedback {
                            Text(feedback)
                                .font(.caption)
                                .foregroundStyle(.secondary)
                        }
                    }
                }
            } else {
                Text("Update the MacCleaner CLI to manage scheduling here.")
                    .foregroundStyle(.secondary)
            }
        }
    }

    /// "Monday 7 Sep, 9:00 am" from the engine's ISO next_run — nil when the
    /// engine predates 2.15 or nothing is scheduled.
    private var nextRunText: String? {
        guard let iso = bridge.scheduleStatus?.next_run,
              let date = CleanerBridge.parseTimestamp(iso) else { return nil }
        return date.formatted(date: .abbreviated, time: .shortened)
    }

    // MARK: - Alerts (notifications + low-disk warning)

    private var appearanceSection: some View {
        SettingsSection(title: "Appearance") {
            Toggle(isOn: Binding(
                get: { bridge.showInDock },
                set: { on in Task { await bridge.setShowInDock(on) } }
            )) {
                VStack(alignment: .leading, spacing: 2) {
                    Text("Show in Dock")
                    Text("MacCleaner stays in the menu bar either way")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }
            }
        }
    }

    private var v3Section: some View {
        SettingsSection(title: "V3 Engine (soak)") {
            Toggle(isOn: Binding(
                get: { bridge.v3SoakEnabled },
                set: { on in Task { await bridge.setV3Soak(on) } }
            )) {
                VStack(alignment: .leading, spacing: 2) {
                    Text("Verify the new Swift engine in the background")
                    Text("Runs read-only beside each full scan and records any disagreement. Never deletes anything.")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }
            }
            if let n = bridge.soakDivergences {
                HStack(spacing: 8) {
                    Image(systemName: n == 0 ? "checkmark.seal" : "exclamationmark.triangle")
                        .foregroundStyle(n == 0 ? Color.green : .orange)
                    Text(n == 0 ? "Last comparison: engines agree"
                                : "Last comparison: \(n) divergence\(n == 1 ? "" : "s")")
                        .font(.callout)
                    if n > 0 {
                        Button("Show Log") {
                            NSWorkspace.shared.selectFile(CleanerBridge.soakLogPath(),
                                                          inFileViewerRootedAtPath: "")
                        }
                        .buttonStyle(.link)
                    }
                }
            }
        }
    }

    private var alertsSection: some View {
        SettingsSection(title: "Alerts") {
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
        }
    }

    // MARK: - Updates (Sparkle scaffold — stub until Task 7)

    @ViewBuilder
    private var updatesSection: some View {
        if updater.available {
            SettingsSection(title: "Updates") {
                LabeledContent("Version", value: currentVersion)
                Toggle("Automatically check for updates", isOn: $updater.automaticChecks)
                if let version = updater.pendingUpdateVersion {
                    // B1: a scheduled (non-user-initiated) check found an
                    // update whose alert Sparkle can't reliably put in front
                    // of the user in this LSUIElement app — mirrors the
                    // MenuBarPanel row so Settings shows the same thing.
                    HStack(spacing: 8) {
                        Image(systemName: "arrow.down.circle.fill")
                            .foregroundStyle(Color.accentCyan)
                        Text("Update to \(version) available")
                        Spacer()
                        Button("Review…") { updater.checkForUpdates() }
                    }
                }
                Button("Check for Updates…") { updater.checkForUpdates() }
                    // "Also fix": mirrors SPUStandardUpdaterController's own
                    // menu-item validation behavior (disable while a check
                    // can't be made) via the KVO-compliant
                    // `canCheckForUpdates` — stub build's `canCheck` is a
                    // fixed `false`.
                    .disabled(!updater.canCheck)
            }
        } else {
            SettingsSection(title: "Updates") {
                Text("Updates ship via Homebrew or GitHub Releases in this build.")
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }
        }
    }

    private var currentVersion: String {
        (Bundle.main.object(forInfoDictionaryKey: "CFBundleShortVersionString") as? String) ?? "—"
    }

    private var scheduleCaption: String {
        if bridge.isSchedulingBusy { return "Updating schedule…" }
        guard let status = bridge.scheduleStatus else { return "" }
        if status.legacy_cron {
            return "A legacy cron schedule exists — choosing an option migrates it to launchd."
        }
        if status.schedule != nil {
            if status.agents.allSatisfy(\.loaded) {
                return "Active — cleans run in the background and notify when done. Low-disk check: hourly."
            }
            // An agent whose load state could not be verified is not a
            // broken schedule — telling the user to reload one that is
            // running fine is the exact false alarm engine 2.14.1 fixed.
            if status.agents.contains(where: { !$0.loaded && !$0.isUnverified }) {
                return "Installed but not loaded — pick the schedule again to reload it."
            }
            return "Installed. Its running state could not be confirmed just now."
        }
        return "No automatic cleanup. Scans and cleans only run when you start them."
    }
}

/// One named glass panel in the Settings list — an uppercase, tracked
/// caption above a `.glassPanel()` card. The caption reuses `Font.sectionLabel`,
/// the same typographic recipe `ReviewBadge` uses (size 10, semibold, 0.5
/// tracking), so "label" text reads consistently across the app; unlike the
/// badge this is a plain heading, not a capsule.
private struct SettingsSection<Content: View>: View {
    let title: String
    @ViewBuilder var content: Content

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text(title.uppercased())
                .font(.sectionLabel)
                .tracking(0.5)
                .foregroundStyle(.secondary)

            VStack(alignment: .leading, spacing: 16) {
                content
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            .padding(16)
            .glassPanel()
        }
    }
}

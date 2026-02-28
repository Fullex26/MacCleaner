import Cocoa
import Foundation

// ── Data models ────────────────────────────────────────────────────────────────
struct CleanerTarget: Codable {
    let category: String
    let label: String
    let size_bytes: Int
    let size_human: String
    let safe: Bool
}

struct CleanerReport: Codable {
    let timestamp: String
    let disk: String
    let total_reclaimable_bytes: Int
    let total_reclaimable_human: String
    let targets: [CleanerTarget]
}

// ── App entry ──────────────────────────────────────────────────────────────────
class AppDelegate: NSObject, NSApplicationDelegate {
    var statusItem: NSStatusItem!
    var lastReport: CleanerReport?
    var timer: Timer?

    // Path to cleaner.py — update this after install
    var cleanerPath: String {
        let home = FileManager.default.homeDirectoryForCurrentUser.path
        return "\(home)/mac-cleaner/cleaner.py"
    }

    func applicationDidFinishLaunching(_ notification: Notification) {
        statusItem = NSStatusBar.system.statusItem(withLength: NSStatusItem.variableLength)
        updateButton(reclaimable: nil)
        buildMenu()
        refreshData()

        // Auto-refresh every 6 hours
        timer = Timer.scheduledTimer(withTimeInterval: 6 * 3600, repeats: true) { [weak self] _ in
            self?.refreshData()
        }
    }

    // ── UI ─────────────────────────────────────────────────────────────────────
    func updateButton(reclaimable: String?) {
        if let btn = statusItem.button {
            if let size = reclaimable {
                btn.title = "🧹 \(size)"
            } else {
                btn.title = "🧹"
            }
        }
    }

    func buildMenu() {
        let menu = NSMenu()

        // Header
        let headerItem = NSMenuItem(title: "MacCleaner", action: nil, keyEquivalent: "")
        headerItem.isEnabled = false
        menu.addItem(headerItem)

        if let report = lastReport {
            menu.addItem(NSMenuItem.separator())

            // Disk status
            let diskItem = NSMenuItem(title: "💾 \(report.disk)", action: nil, keyEquivalent: "")
            diskItem.isEnabled = false
            menu.addItem(diskItem)

            // Reclaimable
            let reclaimItem = NSMenuItem(
                title: "📦 Reclaimable: \(report.total_reclaimable_human)",
                action: nil, keyEquivalent: ""
            )
            reclaimItem.isEnabled = false
            menu.addItem(reclaimItem)

            menu.addItem(NSMenuItem.separator())

            // Top targets
            let topTargets = report.targets.prefix(5)
            for t in topTargets {
                let item = NSMenuItem(
                    title: "  \(categoryIcon(t.category)) \(t.label) — \(t.size_human)",
                    action: nil, keyEquivalent: ""
                )
                item.isEnabled = false
                menu.addItem(item)
            }

            if report.targets.count > 5 {
                let moreItem = NSMenuItem(
                    title: "  … and \(report.targets.count - 5) more",
                    action: nil, keyEquivalent: ""
                )
                moreItem.isEnabled = false
                menu.addItem(moreItem)
            }
        } else {
            let loadingItem = NSMenuItem(title: "Scanning…", action: nil, keyEquivalent: "")
            loadingItem.isEnabled = false
            menu.addItem(loadingItem)
        }

        menu.addItem(NSMenuItem.separator())

        // Actions
        menu.addItem(NSMenuItem(
            title: "🔍 Preview (Terminal)",
            action: #selector(runPreview),
            keyEquivalent: "p"
        ))
        menu.addItem(NSMenuItem(
            title: "🧹 Clean Now (Interactive)",
            action: #selector(runClean),
            keyEquivalent: "c"
        ))
        menu.addItem(NSMenuItem(
            title: "⚡ Auto-Clean Safe Items",
            action: #selector(runAutoClean),
            keyEquivalent: "a"
        ))

        menu.addItem(NSMenuItem.separator())

        menu.addItem(NSMenuItem(
            title: "🔄 Refresh",
            action: #selector(refreshData),
            keyEquivalent: "r"
        ))
        menu.addItem(NSMenuItem(
            title: "⚙️  Open Config",
            action: #selector(openConfig),
            keyEquivalent: ","
        ))

        menu.addItem(NSMenuItem.separator())
        menu.addItem(NSMenuItem(
            title: "Quit MacCleaner",
            action: #selector(NSApplication.terminate(_:)),
            keyEquivalent: "q"
        ))

        // Set targets for all items
        for item in menu.items {
            item.target = self
        }

        statusItem.menu = menu
    }

    func categoryIcon(_ category: String) -> String {
        switch category {
        case "xcode":  return "🔨"
        case "docker": return "🐳"
        case "node":   return "📦"
        case "python": return "🐍"
        case "caches": return "🗑️"
        case "logs":   return "📋"
        default:       return "•"
        }
    }

    // ── Data refresh ───────────────────────────────────────────────────────────
    @objc func refreshData() {
        updateButton(reclaimable: nil)
        DispatchQueue.global(qos: .background).async { [weak self] in
            guard let self = self else { return }
            let output = self.runPython(args: ["--json"])
            guard let data = output.data(using: .utf8),
                  let report = try? JSONDecoder().decode(CleanerReport.self, from: data)
            else {
                DispatchQueue.main.async {
                    self.updateButton(reclaimable: "error")
                    self.buildMenu()
                }
                return
            }
            DispatchQueue.main.async {
                self.lastReport = report
                self.updateButton(reclaimable: report.total_reclaimable_human)
                self.buildMenu()
            }
        }
    }

    // ── Actions ────────────────────────────────────────────────────────────────
    @objc func runPreview() {
        runInTerminal(args: "--preview")
    }

    @objc func runClean() {
        runInTerminal(args: "--clean")
    }

    @objc func runAutoClean() {
        let alert = NSAlert()
        alert.messageText = "Auto-Clean Safe Items?"
        alert.informativeText = "This will automatically delete all safe items without asking. Review items first with Preview."
        alert.addButton(withTitle: "Clean Now")
        alert.addButton(withTitle: "Cancel")
        alert.alertStyle = .warning

        if alert.runModal() == .alertFirstButtonReturn {
            runInTerminal(args: "--clean --yes")
            DispatchQueue.main.asyncAfter(deadline: .now() + 5) {
                self.refreshData()
            }
        }
    }

    @objc func openConfig() {
        let home = FileManager.default.homeDirectoryForCurrentUser.path
        let configPath = "\(home)/mac-cleaner/config.json"
        NSWorkspace.shared.open(URL(fileURLWithPath: configPath))
    }

    // ── Helpers ────────────────────────────────────────────────────────────────
    func runPython(args: [String]) -> String {
        let process = Process()
        process.executableURL = URL(fileURLWithPath: "/usr/bin/python3")
        process.arguments = [cleanerPath] + args

        let pipe = Pipe()
        process.standardOutput = pipe
        process.standardError = Pipe()

        try? process.run()
        process.waitUntilExit()

        return String(data: pipe.fileHandleForReading.readDataToEndOfFile(), encoding: .utf8) ?? ""
    }

    func runInTerminal(args: String) {
        let home = FileManager.default.homeDirectoryForCurrentUser.path
        let cmd = "python3 \(home)/mac-cleaner/cleaner.py \(args)"
        let script = """
        tell application "Terminal"
            activate
            do script "\(cmd)"
        end tell
        """
        var error: NSDictionary?
        NSAppleScript(source: script)?.executeAndReturnError(&error)
    }
}

// ── Main ───────────────────────────────────────────────────────────────────────
let app = NSApplication.shared
let delegate = AppDelegate()
app.delegate = delegate
app.setActivationPolicy(.accessory) // No dock icon, menu bar only
app.run()

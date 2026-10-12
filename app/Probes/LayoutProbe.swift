import SwiftUI
import AppKit

/// CI layout probe: compiled with every app source except `MacCleanerApp.swift`
/// (which owns `@main`), it measures each sidebar section's MINIMUM height —
/// the size SwiftUI reports for the smallest possible proposal.
///
/// Why this exists: the Storage tab rendered with its header gone, the window
/// title drawn over the list and the sidebar apparently empty, across two
/// releases and two "fixes". The cause was one `Text` carrying
/// `.fixedSize(horizontal: false, vertical: true)` outside any scroll view:
/// under a narrow width proposal it wraps a word (or a letter) per line, so the
/// view's minimum height runs to thousands of points. The window's content can
/// then never fit, and AppKit centres the oversized content — pushing the top
/// of BOTH split-view columns (sidebar list, Storage header) above the window.
/// Nothing about that is visible in a code review, so it is measured here.
@main
struct LayoutProbe {
    /// The main window's own `minHeight` (MacCleanerApp.swift). A section whose
    /// minimum height exceeds it can overflow the window it is shown in.
    static let windowMinHeight: CGFloat = 480

    @MainActor
    static func main() {
        _ = NSApplication.shared
        let bridge = CleanerBridge()
        let sections: [(String, AnyView)] = [
            ("Dashboard", AnyView(DashboardView())),
            ("Storage", AnyView(StorageView())),
            ("Projects", AnyView(ProjectsView())),
            ("History", AnyView(HistoryView())),
            ("Settings", AnyView(SettingsView())),
        ]
        var failed = false
        for (name, view) in sections {
            let host = NSHostingController(rootView: view.environmentObject(bridge))
            for width: CGFloat in [0, 120, 300] {
                let size = host.sizeThatFits(in: CGSize(width: width, height: 0))
                let ok = size.height <= windowMinHeight
                print("\(ok ? "ok  " : "FAIL") \(name) @ width \(Int(width)): min height \(Int(size.height))pt")
                if !ok { failed = true }
            }
        }
        if failed {
            print("A section's minimum height exceeds the window's minHeight (\(Int(windowMinHeight))pt): its top will be pushed off-screen. Look for fixedSize(vertical:) or fixed frames outside a ScrollView/List.")
            exit(1)
        }
    }
}

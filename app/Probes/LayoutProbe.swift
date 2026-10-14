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
        func checkBar(_ name: String, _ actual: Double, _ expected: Double) {
            let ok = actual.isFinite && abs(actual - expected) < 0.000001
            print("\(ok ? "ok  " : "FAIL") Storage bar \(name): \(actual), expected \(expected)")
            if !ok { failed = true }
        }
        func entry(_ size: Int) -> StorageMapEntry {
            StorageMapEntry(path: "/fixture/\(size)", name: "\(size)", size_bytes: size,
                            size_human: "fixture", kind: "dir", category: "other")
        }
        let map = StorageMapReport(root: "/fixture", total_bytes: 1000,
                                   total_human: "fixture", category: "other",
                                   children: [entry(100), entry(400), entry(200)])
        let scale = StorageBarScale(map: map)
        checkBar("largest sibling", scale.fraction(bytes: 400), 1)
        checkBar("half largest", scale.fraction(bytes: 200), 0.5)
        checkBar("quarter largest", scale.fraction(bytes: 100), 0.25)
        checkBar("zero", scale.fraction(bytes: 0), 0)
        let empty = StorageMapReport(root: "/fixture", total_bytes: 1000,
                                     total_human: "fixture", category: "other", children: [])
        checkBar("empty level", StorageBarScale(map: empty).fraction(bytes: 0), 0)
        let zero = StorageMapReport(root: "/fixture", total_bytes: 0,
                                    total_human: "fixture", category: "other", children: [entry(0)])
        checkBar("zero level", StorageBarScale(map: zero).fraction(bytes: 0), 0)

        func checkStorageHeight(_ state: String) {
            let host = NSHostingController(rootView: StorageView().environmentObject(bridge))
            for width: CGFloat in [0, 120, 300] {
                let size = host.sizeThatFits(in: CGSize(width: width, height: 0))
                let ok = size.height <= windowMinHeight
                print("\(ok ? "ok  " : "FAIL") Storage \(state) @ width \(Int(width)): min height \(Int(size.height))pt")
                if !ok { failed = true }
            }
        }

        // Additive deadline metadata must survive the app's JSON bridge.
        let incompleteJSON = Data(#"{"root":"/fixture","total_bytes":0,"total_human":"0.0 B","category":"other","children":[],"incomplete":true}"#.utf8)
        do {
            let unavailable = try JSONDecoder().decode(StorageMapReport.self, from: incompleteJSON)
            let encoded = try JSONEncoder().encode(unavailable)
            let object = try JSONSerialization.jsonObject(with: encoded) as? [String: Any]
            let preserved = object?["incomplete"] as? Bool == true
            print("\(preserved ? "ok  " : "FAIL") Storage incomplete measurement metadata")
            if !preserved { failed = true }
            bridge.acceptStorageMap(unavailable)
            checkStorageHeight("unavailable")
            if bridge.storageMap?.totalLabel != "Total unavailable" || bridge.storageMapError == nil {
                print("FAIL Storage unavailable result must show a warning instead of an empty measurement")
                failed = true
            }
            var partial = map
            partial.incomplete = true
            bridge.acceptStorageMap(partial)
            checkStorageHeight("partial")
            if bridge.storageMap?.children.count != 3 || bridge.storageMapError == nil {
                print("FAIL Storage partial result must retain rows and warning")
                failed = true
            }
            let legacyJSON = Data(#"{"root":"/fixture","total_bytes":0,"total_human":"0.0 B","category":"other","children":[]}"#.utf8)
            let legacy = try JSONDecoder().decode(StorageMapReport.self, from: legacyJSON)
            bridge.acceptStorageMap(legacy)
            if bridge.storageMapError != nil || bridge.storageMap?.totalLabel != "0.0 B" {
                print("FAIL Storage complete refresh must clear incomplete state")
                failed = true
            }
            var complete = partial
            complete.incomplete = false
            bridge.acceptStorageMap(complete)
            if bridge.storageMapError != nil || bridge.storageMap?.totalLabel != map.total_human {
                print("FAIL Storage explicit complete flag must restore the total")
                failed = true
            }
        } catch {
            print("FAIL Storage incomplete fixture: \(error)")
            failed = true
        }

        bridge.storageMap = nil
        bridge.storageMapError = nil
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
            print("App probe failed: inspect the FAIL lines above. Oversized section heights indicate fixedSize(vertical:) or fixed frames outside a ScrollView/List.")
            exit(1)
        }
    }
}

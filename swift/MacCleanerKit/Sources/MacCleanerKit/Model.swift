// V3 Stage 2: the read-only model. No deletion API exists in this module on
// purpose — Stage 4 ports the delete guards first, with the adversarial
// suite, before any caller. See docs/V3-SWIFT-ENGINE.md.
import Foundation

public struct Target {
    public let id: String
    public let category: String
    public let label: String
    public let desc: String
    public let safe: Bool
    public let emptyOnly: Bool
    /// HOME-relative ("~/...") as generated; expanded at scan time.
    public let paths: [String]
    public let glob: String?
    public let cmd: String?

    public init(id: String, category: String, label: String, desc: String,
                safe: Bool, emptyOnly: Bool, paths: [String], glob: String?, cmd: String?) {
        self.id = id; self.category = category; self.label = label; self.desc = desc
        self.safe = safe; self.emptyOnly = emptyOnly
        self.paths = paths; self.glob = glob; self.cmd = cmd
    }
}

public struct Config {
    public var enabledCategories: [String]
    public var skipPaths: [String]

    /// Mirrors load_config()'s merge-with-defaults posture for the two keys
    /// Stage 2 needs: a missing/unreadable config means "everything enabled,
    /// nothing skipped" — the parity sandbox always provides a real file.
    public static func load(from path: String?) -> Config {
        var enabled = allTargets.reduce(into: Set<String>()) { $0.insert($1.category) }
        var skip: [String] = []
        if let path, let data = FileManager.default.contents(atPath: path),
           let obj = try? JSONSerialization.jsonObject(with: data) as? [String: Any] {
            if let cats = obj["enabled_categories"] as? [String] { enabled = Set(cats) }
            if let s = obj["skip_paths"] as? [String] { skip = s }
        }
        return Config(enabledCategories: Array(enabled), skipPaths: skip)
    }
}

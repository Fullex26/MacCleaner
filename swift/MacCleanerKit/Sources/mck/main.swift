// mck — MacCleanerKit's read-only CLI (V3 Stage 2).
// Subset of the Python engine's contract: `scan --json --all` and
// `categories --json`. No deletion, by design, until Stage 4.
import Foundation
import MacCleanerKit

let version = "0.1.0-stage2"

func jsonOut(_ obj: Any) {
    let data = try! JSONSerialization.data(
        withJSONObject: obj, options: [.prettyPrinted, .sortedKeys])
    print(String(data: data, encoding: .utf8)!)
}

let args = CommandLine.arguments.dropFirst()
let configPath = ProcessInfo.processInfo.environment["MACCLEANER_CONFIG"]

switch args.first {
case "scan":
    guard args.contains("--json"), args.contains("--all") else {
        FileHandle.standardError.write("mck scan requires --json --all (Stage 2)\n".data(using: .utf8)!)
        exit(2)
    }
    let config = Config.load(from: configPath)
    let results = Scanner.scan(config: config)
    var targets: [[String: Any]] = results.map { r -> [String: Any] in
        [
            "id": r.target.id,
            "category": r.target.category,
            "label": r.target.label,
            "description": r.target.desc,
            "safe": r.target.safe,
            "exists": r.exists,
            "size_bytes": r.sizeBytes,
            "cmd": r.target.cmd != nil,
        ]
    }
    // Dynamic tmp scanner (V3 Stage 2 second half). Mirrors collect_targets:
    // appended only when the category is enabled; always review-only.
    if config.enabledCategories.contains("tmp") {
        let root = ProcessInfo.processInfo.environment["MACCLEANER_TMP_ROOT"] ?? "/private/tmp"
        let hits = TmpScanner.scan(root: root, minAgeDays: config.tmpMinAgeDays,
                                   skipPaths: config.skipPaths)
        for t in TmpScanner.targets(for: hits) {
            targets.append([
                "id": t.id, "category": "tmp", "label": t.label,
                "description": t.desc, "safe": false,
                "exists": true, "size_bytes": Scanner.duBytes(t.path),
                "cmd": false,
            ])
        }
    }
    jsonOut(["version": version, "targets": targets])
case "categories":
    guard args.contains("--json") else { exit(2) }
    let byCat = Dictionary(grouping: allTargets, by: \.category)
    jsonOut([
        "version": version,
        "categories": categoryDescriptions.compactMap { cat -> [String: Any]? in
            guard let ts = byCat[cat.name] else { return nil }
            return [
                "name": cat.name,
                "description": cat.description,
                "targets": ts.map { ["id": $0.id, "label": $0.label, "safe": $0.safe] },
            ]
        },
    ])
default:
    FileHandle.standardError.write("usage: mck scan --json --all | mck categories --json\n".data(using: .utf8)!)
    exit(2)
}

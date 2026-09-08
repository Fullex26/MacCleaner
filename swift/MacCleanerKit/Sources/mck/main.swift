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
    // Simulators: cmd targets assembled from simctl's own identifiers,
    // emitted with the same shape static cmd targets use (presence-only in
    // the parity gate; nothing here ever executes them).
    if config.enabledCategories.contains("simulators") {
        for t in SimulatorScanner.scan(staleDays: config.simulatorStaleDays) {
            targets.append([
                "id": t.id, "category": t.category, "label": t.label,
                "description": t.desc, "safe": false,
                "exists": false, "size_bytes": 0,
                "cmd": true,
            ])
        }
    }
    // Leftovers: multi-path review-only targets, sized like the Python
    // engine sizes them (du per path, summed).
    if config.enabledCategories.contains("leftovers") {
        let hits = LeftoversScanner.scan(minAgeDays: config.appLeftoverMinAgeDays,
                                         skipPaths: config.skipPaths)
        for t in LeftoversScanner.targets(from: hits) {
            let size = t.paths.reduce(Int64(0)) { $0 + Scanner.duBytes($1) }
            targets.append([
                "id": t.id, "category": t.category, "label": t.label,
                "description": t.desc, "safe": false,
                "exists": true, "size_bytes": size,
                "cmd": false,
            ])
        }
    }
    jsonOut(["version": version, "targets": targets])
case "guard-check":
    // Read-only verdict query for the Stage 4 guards — answers "WOULD the
    // guard allow this path", never acts. Exists so the cross-engine parity
    // harness (tools/check_guard_parity.py) can judge the same adversarial
    // corpus with both engines. This is not a deletion API and must not
    // become one: no caller in this binary deletes anything.
    guard args.contains("--json"), let path = args.dropFirst().first(where: { !$0.hasPrefix("--") }) else {
        FileHandle.standardError.write("usage: mck guard-check PATH --json [--tmp-root R]\n".data(using: .utf8)!)
        exit(2)
    }
    let env = ProcessInfo.processInfo.environment
    let home = env["HOME"] ?? NSHomeDirectory()
    let tmpRoot = env["MACCLEANER_TMP_ROOT"] ?? "/private/tmp"
    jsonOut([
        "version": version,
        "path": path,
        "safe_to_delete": Guards.safeToDelete(path, home: home),
        "tmp_scan_path_allowed": Guards.tmpScanPathAllowed(path, tmpRoot: tmpRoot),
    ])
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
    FileHandle.standardError.write("usage: mck scan --json --all | mck categories --json | mck guard-check PATH --json\n".data(using: .utf8)!)
    exit(2)
}

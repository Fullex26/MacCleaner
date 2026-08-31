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
    let results = Scanner.scan(config: Config.load(from: configPath))
    jsonOut([
        "version": version,
        "targets": results.map { r -> [String: Any] in
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
        },
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
    FileHandle.standardError.write("usage: mck scan --json --all | mck categories --json\n".data(using: .utf8)!)
    exit(2)
}

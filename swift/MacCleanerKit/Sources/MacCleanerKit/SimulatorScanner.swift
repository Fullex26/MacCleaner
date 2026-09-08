// V3 Stage 2: simulator dynamic scanner, ported from cleaner.py's
// scan_simulator_targets(). Read-only — it enumerates and PROPOSES cmd
// targets exactly as the Python engine does; nothing in this module executes
// a delete (Stage 4 ports the guards first).
//
// Faithfulness notes, mirrored from the Python source line-for-line where it
// matters:
//  - Deletion (when it eventually happens) goes through simctl, never raw
//    file removal — so hits are cmd targets whose commands are assembled
//    exclusively from simctl's own identifiers.
//  - Every udid/runtime identifier is validated against the same shell-safe
//    shapes before it can appear in a command string; anything else is
//    silently dropped. simctl's JSON is untrusted input from the shell's
//    point of view.
//  - `simctl runtime list -j` has shipped at least three shapes; all three
//    are handled ({"runtimes": [...]}, bare [...], and the Xcode-26 bare
//    dict keyed by image UUID where `identifier` is a UUID and the
//    reverse-DNS string moved to `runtimeIdentifier`).
//  - Missing xcrun / no Xcode degrades to zero targets, never an error.
import Foundation

public enum SimulatorScanner {

    // Same shapes cleaner.py pins: _SIMCTL_UDID_RE and _SIMCTL_RUNTIME_ID_RE
    // (the latter accepting BOTH the reverse-DNS form and the Xcode-26 image
    // UUID form; both remain strictly shell-safe character sets).
    static let udidShape = try! NSRegularExpression(pattern: "^[0-9A-Fa-f-]{8,}$")
    static let runtimeIdShape = try! NSRegularExpression(
        pattern: "^(?:com\\.apple\\.CoreSimulator\\.SimRuntime\\.[A-Za-z0-9.-]+" +
                 "|[0-9A-Fa-f]{8}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{12})$")

    static func matches(_ re: NSRegularExpression, _ s: String) -> Bool {
        re.firstMatch(in: s, range: NSRange(s.startIndex..., in: s)) != nil
    }

    /// `xcrun simctl <args> -j`, parsed; nil on any failure — callers
    /// degrade to no targets, matching how a missing docker degrades.
    static func simctlJSON(_ args: [String]) -> Any? {
        let p = Process()
        p.executableURL = URL(fileURLWithPath: "/usr/bin/env")
        p.arguments = ["xcrun", "simctl"] + args + ["-j"]
        let pipe = Pipe()
        p.standardOutput = pipe
        p.standardError = FileHandle.nullDevice
        guard (try? p.run()) != nil else { return nil }
        let data = pipe.fileHandleForReading.readDataToEndOfFile()
        p.waitUntilExit()
        guard p.terminationStatus == 0 else { return nil }
        return try? JSONSerialization.jsonObject(with: data)
    }

    /// ISO8601 "2026-08-09T00:00:00Z" or with numeric offset, like
    /// _parse_simctl_date. Nil for anything unparseable.
    static func parseDate(_ s: String?) -> TimeInterval? {
        guard let s else { return nil }
        let fmt = DateFormatter()
        fmt.locale = Locale(identifier: "en_US_POSIX")
        fmt.dateFormat = "yyyy-MM-dd'T'HH:mm:ssZZZ"
        return fmt.date(from: s.replacingOccurrences(of: "Z", with: "+0000"))?
            .timeIntervalSince1970
    }

    public static func scan(staleDays: Double, now: TimeInterval = Date().timeIntervalSince1970) -> [Target] {
        var targets: [Target] = []
        guard let devData = simctlJSON(["list", "devices"]) as? [String: Any],
              let devMap = devData["devices"] as? [String: [[String: Any]]] else {
            return targets
        }
        let cutoff = now - staleDays * 86400
        var stale: [[String: Any]] = []
        var staleBytes: Int64 = 0
        var usedRuntimes = Set<String>()

        for (runtimeId, devices) in devMap.sorted(by: { $0.key < $1.key }) {
            for d in devices {
                guard let udid = d["udid"] as? String, matches(udidShape, udid) else { continue }
                usedRuntimes.insert(runtimeId)
                if (d["state"] as? String) == "Booted" { continue }
                var ts = parseDate(d["lastBootedAt"] as? String ?? d["lastUsedAt"] as? String)
                if ts == nil, let dp = d["dataPath"] as? String {
                    var st = stat()
                    if stat(dp, &st) == 0 {
                        ts = TimeInterval(st.st_mtimespec.tv_sec)
                    } else {
                        continue    // Python: unstat-able dataPath -> skip device
                    }
                }
                guard let t = ts, t < cutoff else { continue }
                stale.append(d)
                if let dp = d["dataPath"] as? String {
                    staleBytes += Scanner.duBytes(dp)
                }
            }
        }

        if !stale.isEmpty {
            let udids = stale.compactMap { $0["udid"] as? String }.joined(separator: " ")
            let names = stale.prefix(4).compactMap { $0["name"] as? String ?? "?" }
                .joined(separator: ", ")
            let more = stale.count <= 4 ? "" : " +\(stale.count - 4) more"
            targets.append(Target(
                id: "simulator-stale-devices", category: "simulators",
                label: "Stale simulator devices (\(stale.count))",
                desc: "Not booted in \(Int(staleDays))d: \(names)\(more) — deleted via simctl",
                safe: false, emptyOnly: false, paths: [], glob: nil,
                cmd: "xcrun simctl delete \(udids) 2>/dev/null || true"))
            _ = staleBytes  // sizing surfaced via precomputed_bytes in Stage 3's soak wiring
        }

        guard let rt = simctlJSON(["runtime", "list"]) else { return targets }
        var runtimes: [[String: Any]] = []
        if let arr = rt as? [[String: Any]] {
            runtimes = arr
        } else if let dict = rt as? [String: Any] {
            if let wrapped = dict["runtimes"] as? [[String: Any]] {
                runtimes = wrapped
            } else if let wrappedMap = dict["runtimes"] as? [String: [String: Any]] {
                runtimes = Array(wrappedMap.values)
            } else {
                runtimes = dict.values.compactMap { $0 as? [String: Any] }
            }
        }
        // Deterministic order regardless of dict iteration: sort by identifier.
        let unused = runtimes
            .filter { r in
                let rid = (r["runtimeIdentifier"] as? String) ?? (r["identifier"] as? String)
                guard let ident = r["identifier"] as? String,
                      matches(runtimeIdShape, ident),
                      (r["state"] as? String) == "Ready",
                      let rid, !usedRuntimes.contains(rid) else { return false }
                return true
            }
            .sorted { (($0["identifier"] as? String) ?? "") < (($1["identifier"] as? String) ?? "") }
        if !unused.isEmpty {
            let ids = unused.compactMap { $0["identifier"] as? String }
            let cmd = ids.map { "xcrun simctl runtime delete \($0) 2>/dev/null" }
                .joined(separator: "; ") + "; true"
            targets.append(Target(
                id: "simulator-unused-runtimes", category: "simulators",
                label: "Unused simulator runtimes (\(ids.count))",
                desc: "Runtime images with no simulator devices — re-downloadable in Xcode",
                safe: false, emptyOnly: false, paths: [], glob: nil, cmd: cmd))
        }
        return targets
    }
}

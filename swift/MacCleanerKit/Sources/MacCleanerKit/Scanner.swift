import Foundation

public struct ScannedTarget {
    public let target: Target
    public let exists: Bool
    public let sizeBytes: Int64
}

public enum Scanner {

    static var home: String {
        ProcessInfo.processInfo.environment["HOME"] ?? NSHomeDirectory()
    }

    static func expand(_ path: String) -> String {
        path.hasPrefix("~") ? home + path.dropFirst() : path
    }

    /// Mirrors _target_paths(): glob expansion (sorted, skip-filtered) wins,
    /// then the explicit path list. Uses POSIX glob(3), which matches
    /// Python's glob module for the `*` patterns the table actually uses.
    static func resolvedPaths(_ t: Target, skip: [String]) -> [String] {
        if let pattern = t.glob {
            var g = glob_t()
            defer { globfree(&g) }
            guard glob(expand(pattern), 0, nil, &g) == 0 else { return [] }
            var out: [String] = []
            for i in 0..<Int(g.gl_matchc) {
                if let c = g.gl_pathv[i] { out.append(String(cString: c)) }
            }
            return out.sorted().filter { p in !skip.contains { p.hasPrefix($0) } }
        }
        // add() drops a skip-prefixed entry per path for the multi-path form,
        // and the whole target for the single-path form — filtering per
        // entry here reproduces both, since a single-path target with its
        // one path skipped resolves to [].
        return t.paths.map(expand).filter { p in !skip.contains { p.hasPrefix($0) } }
    }

    static func lexists(_ path: String) -> Bool {
        var st = stat()
        return lstat(path, &st) == 0
    }

    /// `du -skx`, same binary the Python engine uses — Stage 2 verifies
    /// resolution and contract shape; measurement itself is shared plumbing.
    static func duBytes(_ path: String) -> Int64 {
        let p = Process()
        p.executableURL = URL(fileURLWithPath: "/usr/bin/env")
        p.arguments = ["du", "-skx", path]
        let pipe = Pipe()
        p.standardOutput = pipe
        p.standardError = FileHandle.nullDevice
        guard (try? p.run()) != nil else { return 0 }
        let data = pipe.fileHandleForReading.readDataToEndOfFile()
        p.waitUntilExit()
        guard let line = String(data: data, encoding: .utf8),
              let kb = Int64(line.split(separator: "\t").first ?? "") else { return 0 }
        return kb * 1024
    }

    public static func scan(config: Config) -> [ScannedTarget] {
        let enabled = Set(config.enabledCategories)
        var out: [ScannedTarget] = []
        for t in allTargets where enabled.contains(t.category) {
            if t.cmd != nil {
                // Stage 2 never executes estimate commands; presence only.
                out.append(ScannedTarget(target: t, exists: false, sizeBytes: 0))
                continue
            }
            let paths = resolvedPaths(t, skip: config.skipPaths)
            let existing = paths.filter(lexists)
            let size = existing.reduce(Int64(0)) { $0 + duBytes($1) }
            out.append(ScannedTarget(target: t, exists: !existing.isEmpty, sizeBytes: size))
        }
        return out.sorted { $0.target.id < $1.target.id }
    }
}

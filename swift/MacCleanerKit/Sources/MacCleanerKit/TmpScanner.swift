// V3 Stage 2: port of the Python tmp scanner (scan_tmp_artifacts and
// friends). Read-only, like everything in this module — it OFFERS
// candidates; deletion stays Python-only until Stage 4.
//
// Every rule here mirrors a named Python function; when the two disagree,
// tools/check_swift_parity.py fails CI. Do not "improve" one side alone.
import Foundation

public enum TmpKind: String {
    case derivedData = "derived-data"
    case repoClone = "repo-clone"
}

public struct TmpHit {
    public let path: String
    public let kind: TmpKind
}

let tmpCloneManifests: Set<String> = [
    "package.json", "Cargo.toml", "pyproject.toml", "go.mod", "Gemfile",
    "pubspec.yaml", "composer.json", "Package.swift", "pom.xml",
    "build.gradle", "build.gradle.kts", "requirements.txt",
]
let tmpCloneArtifacts: Set<String> = [
    "build", "Build", ".build", "DerivedData", "node_modules", "target",
    "dist", ".next", "Pods", ".venv", "venv",
]
let tmpActivePrefixes = ["claude-"]

public enum TmpScanner {
    static let fm = FileManager.default

    static func isDir(_ p: String) -> Bool {
        var d: ObjCBool = false
        return fm.fileExists(atPath: p, isDirectory: &d) && d.boolValue
    }

    static func names(_ p: String) -> Set<String>? {
        (try? fm.contentsOfDirectory(atPath: p)).map(Set.init)
    }

    /// Mirrors _classify_tmp_dir: content, never name.
    public static func classify(_ p: String) -> TmpKind? {
        if isDir(p + "/Build/Intermediates.noindex") { return .derivedData }
        if isDir(p + "/Logs/Build"),
           let logs = names(p + "/Logs/Build"),
           logs.contains(where: { $0.hasSuffix(".xcactivitylog") }) {
            return .derivedData
        }
        // Build/ + Index.noindex/ plus one corroborating Xcode-only marker
        // (info.plist deliberately NOT required — Xcode omits it under a
        // custom -derivedDataPath; see the 2.14.0 note in cleaner.py).
        if isDir(p + "/Build"), isDir(p + "/Index.noindex") {
            let corroborating = ["info.plist", "ModuleCache.noindex",
                                 "SDKStatCaches.noindex", "CompilationCache.noindex",
                                 "Logs", "SourcePackages"]
            if corroborating.contains(where: { fm.fileExists(atPath: p + "/" + $0) }) {
                return .derivedData
            }
        }
        if fm.fileExists(atPath: p + "/.git"), let entries = names(p) {
            let hasManifest = !entries.isDisjoint(with: tmpCloneManifests)
                || entries.contains { $0.hasSuffix(".xcodeproj") }
            if hasManifest, !entries.isDisjoint(with: tmpCloneArtifacts) {
                return .repoClone
            }
        }
        return nil
    }

    /// Mirrors _running_command_lines: nil means "could not ask", which is
    /// NOT evidence of idleness — see pathIsInUse.
    static func runningCommandLines() -> [String]? {
        let p = Process()
        p.executableURL = URL(fileURLWithPath: "/usr/bin/env")
        p.arguments = ["ps", "-axo", "command="]
        let pipe = Pipe()
        p.standardOutput = pipe
        p.standardError = FileHandle.nullDevice
        guard (try? p.run()) != nil else { return nil }
        let data = pipe.fileHandleForReading.readDataToEndOfFile()
        p.waitUntilExit()
        guard p.terminationStatus == 0,
              let out = String(data: data, encoding: .utf8) else { return nil }
        return out.split(separator: "\n", omittingEmptySubsequences: true).map(String.init)
    }

    /// Mirrors _own_process_tree: a check must not see its own reflection.
    static func ownProcessTree() -> Set<String> {
        var own = Set<String>()
        var pid = ProcessInfo.processInfo.processIdentifier
        for _ in 0..<12 {
            let p = Process()
            p.executableURL = URL(fileURLWithPath: "/usr/bin/env")
            p.arguments = ["ps", "-o", "ppid=,command=", "-p", String(pid)]
            let pipe = Pipe()
            p.standardOutput = pipe
            p.standardError = FileHandle.nullDevice
            guard (try? p.run()) != nil else { break }
            let data = pipe.fileHandleForReading.readDataToEndOfFile()
            p.waitUntilExit()
            guard let line = String(data: data, encoding: .utf8)?
                    .trimmingCharacters(in: .whitespacesAndNewlines),
                  !line.isEmpty else { break }
            let parts = line.split(separator: " ", maxSplits: 1,
                                   omittingEmptySubsequences: true)
            guard parts.count == 2, let ppid = Int32(parts[0]) else { break }
            own.insert(String(parts[1]).trimmingCharacters(in: .whitespaces))
            pid = ppid
            if pid <= 1 { break }
        }
        return own
    }

    /// Mirrors _path_is_in_use: boundary-aware substring match over the
    /// process list, own tree excluded, nil commands -> false (degrade open;
    /// these targets are review-only and the age gate remains the guard).
    public static func pathIsInUse(_ path: String, commands: [String]?,
                                   own: Set<String>) -> Bool {
        guard let commands, !commands.isEmpty else { return false }
        for line in commands {
            if own.contains(line.trimmingCharacters(in: .whitespaces)) { continue }
            var search = line.startIndex
            while let r = line.range(of: path, range: search..<line.endIndex) {
                if r.upperBound == line.endIndex {
                    return true
                }
                let next = line[r.upperBound]
                if !(next.isLetter || next.isNumber || next == "-" || next == "_" || next == ".") {
                    return true
                }
                search = line.index(after: r.lowerBound)
            }
        }
        return false
    }

    struct EntryStat {
        let isSymlink: Bool
        let isDir: Bool
        let uid: uid_t
        let mtime: TimeInterval
    }

    static func lstatEntry(_ p: String) -> EntryStat? {
        var st = stat()
        guard lstat(p, &st) == 0 else { return nil }
        return EntryStat(isSymlink: (st.st_mode & S_IFMT) == S_IFLNK,
                         isDir: (st.st_mode & S_IFMT) == S_IFDIR,
                         uid: st.st_uid,
                         mtime: TimeInterval(st.st_mtimespec.tv_sec))
    }

    /// Mirrors _nested_build_dirs: exactly one level, derived-data shape
    /// only, same age cutoff, liveness-guarded.
    static func nestedBuildDirs(_ parent: String, cutoff: TimeInterval,
                                commands: [String]?, own: Set<String>) -> [String] {
        guard let entries = try? fm.contentsOfDirectory(atPath: parent) else { return [] }
        var found: [String] = []
        let uid = getuid()
        for name in entries {
            let p = parent + "/" + name
            guard let st = lstatEntry(p), st.isDir, !st.isSymlink,
                  st.uid == uid, st.mtime <= cutoff,
                  classify(p) == .derivedData,
                  !pathIsInUse(p, commands: commands, own: own) else { continue }
            found.append(p)
        }
        return found
    }

    public static func scan(root: String, minAgeDays: Double, skipPaths: [String]) -> [TmpHit] {
        guard let entries = try? fm.contentsOfDirectory(atPath: root) else { return [] }
        let cutoff = Date().timeIntervalSince1970 - minAgeDays * 86400
        let uid = getuid()
        let commands = runningCommandLines()
        let own = ownProcessTree()
        var hits: [TmpHit] = []
        let resolvedSkips = skipPaths.map { (($0 as NSString).expandingTildeInPath as NSString).resolvingSymlinksInPath }
        for name in entries {
            let p = root + "/" + name
            guard let st = lstatEntry(p), st.isDir, !st.isSymlink else { continue }
            if tmpActivePrefixes.contains(where: { name.hasPrefix($0) }) { continue }
            guard st.uid == uid, st.mtime <= cutoff else { continue }
            let resolved = (p as NSString).resolvingSymlinksInPath
            if resolvedSkips.contains(where: { resolved == $0 || resolved.hasPrefix($0 + "/") }) { continue }
            if pathIsInUse(p, commands: commands, own: own) { continue }
            if let kind = classify(p) {
                hits.append(TmpHit(path: p, kind: kind))
                continue
            }
            for child in nestedBuildDirs(p, cutoff: cutoff, commands: commands, own: own) {
                hits.append(TmpHit(path: child, kind: .derivedData))
            }
        }
        return hits.sorted { $0.path < $1.path }
    }

    /// Mirrors slugify(): lowercase, non-[a-z0-9] runs -> "-", collapsed, trimmed.
    public static func slugify(_ text: String) -> String {
        var out = ""
        var lastDash = false
        for ch in text.lowercased() {
            if ("a"..."z").contains(String(ch)) || ("0"..."9").contains(String(ch)) {
                out.append(ch); lastDash = false
            } else if !lastDash {
                out.append("-"); lastDash = true
            }
        }
        return out.trimmingCharacters(in: CharacterSet(charactersIn: "-"))
    }

    /// Mirrors tmp_to_targets' id/label/description derivation.
    public static func targets(for hits: [TmpHit]) -> [(id: String, label: String, desc: String, path: String)] {
        var seen = Set<String>()
        return hits.map { h in
            let name = (h.path as NSString).lastPathComponent
            let base = "tmp-" + slugify(name)
            var tid = base, n = 2
            while seen.contains(tid) { tid = "\(base)-\(n)"; n += 1 }
            seen.insert(tid)
            let kindDesc = h.kind == .derivedData
                ? "Xcode-style derived build products"
                : "Stale repo clone containing build artifacts"
            return (id: tid, label: "/tmp: \(name)",
                    desc: "\(kindDesc); left in /private/tmp by a tool or AI session",
                    path: h.path)
        }
    }
}

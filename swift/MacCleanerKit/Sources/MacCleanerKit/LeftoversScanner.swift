// V3 Stage 2: app-leftovers dynamic scanner, ported from cleaner.py's
// scan_app_leftovers() family. Read-only: it enumerates orphaned per-app
// data under ~/Library and proposes review-only targets; no deletion API
// exists in this module (Stage 4 ports the guards first).
//
// Semantics mirrored from the Python source:
//  - Detection is bundle-ID-precise, never fuzzy: only reverse-DNS-shaped
//    names count, and only in the five roots Apple's conventions key by
//    bundle ID. Each root has a REAL-WORLD shape (dir vs file, and which
//    suffix to strip); a wrong-shaped entry is skipped, never guessed at.
//  - Installed enumeration reads top-level .app bundles in the configured
//    app roots PLUS .apps exactly one level inside a non-.app wrapper
//    (Adobe-style). A symlinked *.app IS followed (read-only enumeration;
//    refusing could only manufacture false positives) — a symlinked
//    wrapper folder is NOT.
//  - Apple's own domains, MacCleaner itself, and strict dot-boundary
//    sub-domains of installed IDs are always excluded.
//  - Spotlight (mdfind) is a batched second-opinion pass that only ever
//    NARROWS the hit list; any mdfind failure degrades to a no-op.
//  - Candidates newer than the age gate are dropped; symlinked entries are
//    never candidates.
import Foundation

public enum LeftoversScanner {

    static let roots = ["Caches", "Preferences", "Saved Application State",
                        "HTTPStorages", "WebKit"]
    static let excludePrefixes = ["com.apple.", "group.com.apple."]
    static let excludeExact: Set<String> = ["com.fullex.maccleaner"]
    static let bundleIdShape = try! NSRegularExpression(
        pattern: "^[a-z0-9]+(\\.[a-z0-9-]+)+$")

    static func looksLikeBundleId(_ name: String) -> Bool {
        let lower = name.lowercased()
        return bundleIdShape.firstMatch(
            in: lower, range: NSRange(lower.startIndex..., in: lower)) != nil
    }

    static func excluded(_ bundleId: String) -> Bool {
        if excludeExact.contains(bundleId) { return true }
        return excludePrefixes.contains { bundleId.hasPrefix($0) }
    }

    static func ownedByInstalled(_ candidate: String, _ installed: Set<String>) -> Bool {
        if installed.contains(candidate) { return true }
        // Dot boundary required: "com.example.appfoo" is NOT owned by
        // installed "com.example.app".
        return installed.contains { candidate.hasPrefix($0 + ".") }
    }

    static func libraryRoot() -> String {
        ProcessInfo.processInfo.environment["MACCLEANER_LEFTOVER_LIBRARY_ROOT"]
            ?? Scanner.home + "/Library"
    }

    static func installedAppsDirs() -> [String] {
        let raw = ProcessInfo.processInfo.environment["MACCLEANER_INSTALLED_APPS_DIRS"]
            ?? "/Applications:\(Scanner.home)/Applications:/System/Applications"
        return raw.split(separator: ":").map(String.init).filter { !$0.isEmpty }
    }

    /// CFBundleIdentifier from Contents/Info.plist, lowercased; nil on any
    /// failure. Never throws out of here.
    static func bundleIdentifier(ofApp path: String) -> String? {
        let plist = path + "/Contents/Info.plist"
        guard let data = FileManager.default.contents(atPath: plist),
              let obj = try? PropertyListSerialization.propertyList(from: data, format: nil),
              let dict = obj as? [String: Any],
              let bid = dict["CFBundleIdentifier"] as? String, !bid.isEmpty else { return nil }
        return bid.lowercased()
    }

    static func isDirNoFollow(_ path: String) -> Bool {
        var st = stat()
        return lstat(path, &st) == 0 && (st.st_mode & S_IFMT) == S_IFDIR
    }

    static func isDirFollow(_ path: String) -> Bool {
        var d: ObjCBool = false
        return FileManager.default.fileExists(atPath: path, isDirectory: &d) && d.boolValue
    }

    static func collectAppBundleId(_ path: String, _ name: String, into ids: inout Set<String>) {
        guard name.hasSuffix(".app"), isDirFollow(path) else { return }
        if let bid = bundleIdentifier(ofApp: path) { ids.insert(bid) }
    }

    public static func installedBundleIds() -> Set<String> {
        var ids = Set<String>()
        let fm = FileManager.default
        for root in installedAppsDirs() {
            guard let entries = try? fm.contentsOfDirectory(atPath: root) else { continue }
            for name in entries {
                let path = root + "/" + name
                if name.hasSuffix(".app") {
                    collectAppBundleId(path, name, into: &ids)
                    continue
                }
                // One level inside a non-.app wrapper, never following a
                // symlinked wrapper, no deeper recursion.
                guard isDirNoFollow(path),
                      let subs = try? fm.contentsOfDirectory(atPath: path) else { continue }
                for sub in subs {
                    collectAppBundleId(path + "/" + sub, sub, into: &ids)
                }
            }
        }
        return ids
    }

    /// The bundle-id-shaped stem for an entry under one of the five roots,
    /// or nil when the entry doesn't match that root's real-world shape.
    /// Kept in lockstep with `roots`: an unknown root yields nil, never an
    /// inherited rule.
    static func candidate(rootName: String, name: String, path: String) -> String? {
        var st = stat()
        guard lstat(path, &st) == 0 else { return nil }
        let mode = st.st_mode & S_IFMT
        let isDir = mode == S_IFDIR
        let isFile = mode == S_IFREG

        switch rootName {
        case "Preferences":
            if isFile && name.hasSuffix(".plist") { return String(name.dropLast(".plist".count)) }
            return nil
        case "Saved Application State":
            if isDir && name.hasSuffix(".savedState") { return String(name.dropLast(".savedState".count)) }
            return nil
        case "HTTPStorages":
            if isDir && !name.hasSuffix(".binarycookies") { return name }
            if isFile && name.hasSuffix(".binarycookies") { return String(name.dropLast(".binarycookies".count)) }
            return nil
        case "Caches", "WebKit":
            return isDir ? name : nil
        default:
            return nil
        }
    }

    /// Batched Spotlight confirmation; empty set on ANY failure — this pass
    /// only ever narrows the hit list, never blocks the scanner.
    static func mdfindConfirmed(_ candidates: [String]) -> Set<String> {
        let cands = candidates.filter { !$0.isEmpty }
        guard !cands.isEmpty else { return [] }
        let query = cands.map { "kMDItemCFBundleIdentifier == '\($0)'c" }
            .joined(separator: " || ")
        let p = Process()
        p.executableURL = URL(fileURLWithPath: "/usr/bin/env")
        p.arguments = ["mdfind", query]
        let pipe = Pipe()
        p.standardOutput = pipe
        p.standardError = FileHandle.nullDevice
        guard (try? p.run()) != nil else { return [] }
        let data = pipe.fileHandleForReading.readDataToEndOfFile()
        p.waitUntilExit()
        guard p.terminationStatus == 0,
              let out = String(data: data, encoding: .utf8) else { return [] }
        var confirmed = Set<String>()
        for line in out.split(separator: "\n") {
            let path = line.trimmingCharacters(in: .whitespaces)
            if path.isEmpty { continue }
            if let bid = bundleIdentifier(ofApp: path) { confirmed.insert(bid) }
        }
        return confirmed
    }

    public struct Hit {
        public let bundleId: String
        public let paths: [String]
        public let locations: [String]
    }

    public static func scan(minAgeDays: Double, skipPaths: [String],
                            now: TimeInterval = Date().timeIntervalSince1970) -> [Hit] {
        let installed = installedBundleIds()
        let lib = libraryRoot()
        let cutoff = now - minAgeDays * 86400
        let fm = FileManager.default
        let skip = skipPaths.map {
            (Scanner.expand($0) as NSString).resolvingSymlinksInPath
        }

        struct Acc { var paths: [String] = []; var locations: [String] = []; var mtime: TimeInterval = 0 }
        var byId: [String: Acc] = [:]

        for rootName in roots {
            let root = lib + "/" + rootName
            guard let entries = try? fm.contentsOfDirectory(atPath: root) else { continue }
            for name in entries.sorted() {
                let path = root + "/" + name
                guard let raw = candidate(rootName: rootName, name: name, path: path)
                else { continue }
                let cand = raw.lowercased()
                guard looksLikeBundleId(cand), !excluded(cand),
                      !ownedByInstalled(cand, installed) else { continue }
                var st = stat()
                guard lstat(path, &st) == 0, (st.st_mode & S_IFMT) != S_IFLNK else { continue }
                let resolved = (path as NSString).resolvingSymlinksInPath
                if skip.contains(where: { resolved == $0 || resolved.hasPrefix($0 + "/") }) {
                    continue
                }
                var acc = byId[cand] ?? Acc()
                acc.paths.append(path)
                acc.locations.append(rootName)
                acc.mtime = max(acc.mtime, TimeInterval(st.st_mtimespec.tv_sec))
                byId[cand] = acc
            }
        }

        let confirmed = mdfindConfirmed(Array(byId.keys))
        return byId
            .filter { $0.value.mtime <= cutoff && !confirmed.contains($0.key) }
            .map { Hit(bundleId: $0.key, paths: $0.value.paths, locations: $0.value.locations) }
            .sorted { $0.bundleId < $1.bundleId }
    }

    public static func targets(from hits: [Hit]) -> [Target] {
        var seen = Set<String>()
        return hits.map { h in
            let base = "leftover-" + Slug.slugify(h.bundleId)
            var tid = base, n = 2
            while seen.contains(tid) { tid = "\(base)-\(n)"; n += 1 }
            seen.insert(tid)
            return Target(
                id: tid, category: "leftovers",
                label: "App leftovers: \(h.bundleId)",
                desc: "Found in: \(h.locations.joined(separator: ", ")) — no installed app matches this bundle ID; review before deleting",
                safe: false, emptyOnly: false, paths: h.paths, glob: nil, cmd: nil)
        }
    }
}

/// slugify() from cleaner.py: lowercase, non-alphanumerics to "-", runs of
/// "-" collapsed, trimmed.
public enum Slug {
    public static func slugify(_ text: String) -> String {
        var out = ""
        var lastDash = false
        for ch in text.lowercased() {
            if ch.isLetter && ch.isASCII || ch.isNumber && ch.isASCII {
                out.append(ch); lastDash = false
            } else if !lastDash {
                out.append("-"); lastDash = true
            }
        }
        return out.trimmingCharacters(in: CharacterSet(charactersIn: "-"))
    }
}

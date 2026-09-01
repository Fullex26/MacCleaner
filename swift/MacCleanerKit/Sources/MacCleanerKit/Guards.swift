// V3 Stage 4, first half: the deletion GUARDS, ported before any deletion
// API exists. These are pure verdict functions — nothing in this file (or
// module) removes, moves, or modifies anything. Per docs/V3-SWIFT-ENGINE.md,
// deletion callers may only be written once these guards hold verdict-level
// parity with the Python engine across the full adversarial corpus
// (tools/check_guard_parity.py, run in CI).
//
// Fidelity notes, mirroring cleaner.py precisely:
//
// _safe_to_delete (2.8.1 semantics): the PARENT is resolved, the leaf is
// deliberately left unresolved. Rationale ported verbatim: a lexical check
// alone lets a target that is lexically inside $HOME reach its parent
// through a symlinked ANCESTOR and smuggle a delete outside $HOME — the
// near-miss that motivated the 48-scenario attack suite. The leaf stays
// unresolved because deletion unlinks a symlink leaf rather than following
// it: a link whose own directory entry is inside $HOME is safe to remove
// even when it points elsewhere — only the link dies, never its target.
//
// _tmp_scan_path_allowed (2.14.1 semantics): the single narrow carve-out to
// the home-only rule. Fully-resolved path must be a direct child of the
// resolved tmp scan root OR one level below it (the nested build tree inside
// a session workspace); the root itself and anything deeper is refused.
// There is deliberately no knob that widens this.
import Foundation

public enum Guards {

    /// Python's Path.resolve() is non-strict: it resolves symlinks for as
    /// much of the path as exists, then normalizes the nonexistent tail
    /// lexically. realpath(3) instead fails on the first missing component,
    /// so this reimplements the non-strict behaviour — the guards must give
    /// identical verdicts for paths that do not (yet) exist.
    static func nonStrictResolve(_ path: String) -> String {
        var resolved = ""
        var rest = path.hasPrefix("/") ? String(path.dropFirst()) : path
        // Start from "/" for absolute paths; relative paths are made
        // absolute against cwd first, as Path.resolve() does.
        if !path.hasPrefix("/") {
            rest = FileManager.default.currentDirectoryPath.dropFirst() + "/" + rest
        }
        resolved = "/"
        var seenLinks = 0
        var components = rest.split(separator: "/").map(String.init)
        var i = 0
        while i < components.count {
            let c = components[i]
            i += 1
            if c == "" || c == "." { continue }
            if c == ".." {
                // Lexical parent of the resolved-so-far prefix, matching
                // posixpath's non-strict tail normalization.
                if resolved != "/" {
                    resolved = (resolved as NSString).deletingLastPathComponent
                    if resolved.isEmpty { resolved = "/" }
                }
                continue
            }
            let candidate = resolved == "/" ? "/" + c : resolved + "/" + c
            var st = stat()
            if lstat(candidate, &st) == 0 && (st.st_mode & S_IFMT) == S_IFLNK {
                seenLinks += 1
                if seenLinks > 40 { return candidate }   // cycle bail, like ELOOP
                var buf = [CChar](repeating: 0, count: 4096)
                let n = readlink(candidate, &buf, buf.count - 1)
                if n > 0 {
                    let dest = String(cString: Array(buf[0..<n]) + [0])
                    if dest.hasPrefix("/") {
                        resolved = "/"
                        components.replaceSubrange(i..<i, with: dest.split(separator: "/").map(String.init))
                    } else {
                        components.replaceSubrange(i..<i, with: dest.split(separator: "/").map(String.init))
                    }
                    continue
                }
                resolved = candidate
            } else {
                resolved = candidate
            }
        }
        return resolved
    }

    /// Port of _safe_to_delete. Parent resolved, leaf appended unresolved.
    public static func safeToDelete(_ path: String, home: String) -> Bool {
        let p = path.hasSuffix("/") && path != "/" ? String(path.dropLast()) : path
        let name = (p as NSString).lastPathComponent
        if name.isEmpty || name == ".." { return false }
        let parent = (p as NSString).deletingLastPathComponent
        let rp = nonStrictResolve(parent.isEmpty ? "/" : parent) + "/" + name
        let normalizedRp = rp.replacingOccurrences(of: "//", with: "/")
        let resolvedHome = nonStrictResolve(home)
        let trimmed = normalizedRp == "/" ? "/" :
            (normalizedRp.hasSuffix("/") ? String(normalizedRp.dropLast()) : normalizedRp)
        if trimmed.isEmpty || trimmed == "/" { return false }
        let homeTrimmed = resolvedHome.hasSuffix("/") ? String(resolvedHome.dropLast()) : resolvedHome
        if trimmed == homeTrimmed { return false }
        return trimmed.hasPrefix(homeTrimmed + "/")
    }

    /// Port of _tmp_scan_path_allowed: direct child or grandchild of the
    /// resolved tmp scan root; the root itself and deeper levels refused.
    public static func tmpScanPathAllowed(_ path: String, tmpRoot: String) -> Bool {
        let rp = nonStrictResolve(path)
        let root = nonStrictResolve(tmpRoot)
        if rp == root { return false }
        let parent = (rp as NSString).deletingLastPathComponent
        if parent == root { return true }
        let grandparent = (parent as NSString).deletingLastPathComponent
        return grandparent == root
    }
}

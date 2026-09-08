// V3 Stage 4 guard unit tests. tools/check_guard_parity.py is the primary
// gate (both engines judged against the shared adversarial corpus); these
// pin the same named attacks at unit level so a regression is caught by
// `swift test` alone, without needing the Python engine present.
//
// The symlinked-ancestor case is the load-bearing one: it is the 2.8.1
// near-miss — a path lexically inside $HOME whose parent chain passes
// through a symlink to a directory physically outside $HOME. The Python
// guard was nearly shipped without catching it; the port must never lose it.
import XCTest
@testable import MacCleanerKit

final class GuardsTests: XCTestCase {
    var root: URL!
    var home: String!
    var outside: String!

    override func setUpWithError() throws {
        root = URL(fileURLWithPath: NSTemporaryDirectory())
            .appendingPathComponent("guards-\(UUID().uuidString)")
        let fm = FileManager.default
        home = root.appendingPathComponent("home").path
        outside = root.appendingPathComponent("outside").path
        try fm.createDirectory(atPath: home + "/Library/Caches", withIntermediateDirectories: true)
        try fm.createDirectory(atPath: outside, withIntermediateDirectories: true)
        try "victim".write(toFile: outside + "/victim.txt", atomically: true, encoding: .utf8)
    }

    override func tearDownWithError() throws {
        try? FileManager.default.removeItem(at: root)
    }

    // ── the 2.8.1 attack, per the peer session's explicit request ──

    func testSymlinkedAncestorIsRefused() throws {
        // home/Library/Caches/Vendor -> outside; the victim path is
        // lexically inside home but physically outside.
        try FileManager.default.createSymbolicLink(
            atPath: home + "/Library/Caches/Vendor", withDestinationPath: outside)
        XCTAssertFalse(
            Guards.safeToDelete(home + "/Library/Caches/Vendor/victim.txt", home: home),
            "a symlinked ancestor must never smuggle a delete outside $HOME")
    }

    func testSymlinkChainAncestorIsRefused() throws {
        try FileManager.default.createSymbolicLink(
            atPath: root.appendingPathComponent("hop").path, withDestinationPath: outside)
        try FileManager.default.createSymbolicLink(
            atPath: home + "/chain", withDestinationPath: root.appendingPathComponent("hop").path)
        XCTAssertFalse(Guards.safeToDelete(home + "/chain/victim.txt", home: home))
    }

    func testSymlinkLeafPointingOutsideIsAllowed() throws {
        // Deleting unlinks the LINK, never its target — the one asymmetry
        // that makes the parent-resolved/leaf-unresolved split correct.
        try FileManager.default.createSymbolicLink(
            atPath: home + "/leaf-link", withDestinationPath: outside + "/victim.txt")
        XCTAssertTrue(Guards.safeToDelete(home + "/leaf-link", home: home))
    }

    // ── the boring-but-load-bearing refusals ──

    func testRefusesHomeItselfAndRoot() {
        XCTAssertFalse(Guards.safeToDelete(home, home: home))
        XCTAssertFalse(Guards.safeToDelete(home + "/", home: home))
        XCTAssertFalse(Guards.safeToDelete("/", home: home))
    }

    func testRefusesOutsideAndSiblingPrefix() {
        XCTAssertFalse(Guards.safeToDelete(outside + "/victim.txt", home: home))
        // "…/home2" starts with the string "…/home" but is not inside it.
        XCTAssertFalse(Guards.safeToDelete(home + "2", home: home))
    }

    func testRefusesDotDotShapes() {
        XCTAssertFalse(Guards.safeToDelete(home + "/Library/..", home: home))
        XCTAssertFalse(Guards.safeToDelete(
            home + "/Library/../../outside/victim.txt", home: home))
    }

    func testAllowsInsideIncludingNonexistent() {
        XCTAssertTrue(Guards.safeToDelete(home + "/Library/Caches", home: home))
        XCTAssertTrue(Guards.safeToDelete(home + "/not-created-yet", home: home))
    }

    // ── tmp carve-out: two levels exactly ──

    func testTmpCarveOutDepthBounds() throws {
        let tmpRoot = root.appendingPathComponent("tmp-root").path
        try FileManager.default.createDirectory(
            atPath: tmpRoot + "/ws/derived/Build", withIntermediateDirectories: true)
        XCTAssertFalse(Guards.tmpScanPathAllowed(tmpRoot, tmpRoot: tmpRoot),
                       "the root itself is never deletable")
        XCTAssertTrue(Guards.tmpScanPathAllowed(tmpRoot + "/ws", tmpRoot: tmpRoot))
        XCTAssertTrue(Guards.tmpScanPathAllowed(tmpRoot + "/ws/derived", tmpRoot: tmpRoot))
        XCTAssertFalse(Guards.tmpScanPathAllowed(tmpRoot + "/ws/derived/Build", tmpRoot: tmpRoot),
                       "three levels deep is refused; depth bounds the blast radius")
        XCTAssertFalse(Guards.tmpScanPathAllowed(outside, tmpRoot: tmpRoot))
    }

    func testTmpRootSymlinkResolves() throws {
        // /tmp -> /private/tmp style aliasing must not defeat the depth check.
        let tmpRoot = root.appendingPathComponent("tmp-root").path
        try? FileManager.default.createDirectory(atPath: tmpRoot + "/ws",
                                                 withIntermediateDirectories: true)
        let alias = root.appendingPathComponent("tmp-alias").path
        try FileManager.default.createSymbolicLink(atPath: alias, withDestinationPath: tmpRoot)
        XCTAssertTrue(Guards.tmpScanPathAllowed(alias + "/ws", tmpRoot: tmpRoot))
    }
}

// Unit tests for the semantics that tools/check_swift_parity.py can't
// easily force divergent cases for (the parity sandbox can't fabricate a
// hostile process list). Each mirrors a named Python behaviour; if Python
// changes, parity fails CI and THESE must be updated in the same change.
import XCTest
@testable import MacCleanerKit

final class TmpScannerTests: XCTestCase {

    // ── pathIsInUse: the 2.14.2 liveness guard ──

    func testBoundaryMatchDoesNotShieldSiblingPrefix() {
        // /tmp/ws named in a command line must flag /tmp/ws, not /tmp/ws2.
        let cmds = ["xcodebuild -derivedDataPath /tmp/ws/derived build"]
        XCTAssertTrue(TmpScanner.pathIsInUse("/tmp/ws", commands: cmds, own: []))
        XCTAssertFalse(TmpScanner.pathIsInUse("/tmp/w", commands: cmds, own: []))
        let cmds2 = ["tool --path /tmp/ws2/build"]
        XCTAssertFalse(TmpScanner.pathIsInUse("/tmp/ws", commands: cmds2, own: []))
    }

    func testPathAtEndOfCommandLineMatches() {
        XCTAssertTrue(TmpScanner.pathIsInUse("/tmp/ws",
                                             commands: ["du -sk /tmp/ws"], own: []))
    }

    func testOwnProcessTreeIsExcluded() {
        // A check must not see its own reflection (observed for real: the
        // asking shell's command line contained the asked-about path).
        let line = "sh -c ls /tmp/ws"
        XCTAssertFalse(TmpScanner.pathIsInUse("/tmp/ws", commands: [line], own: [line]))
        XCTAssertTrue(TmpScanner.pathIsInUse("/tmp/ws", commands: [line], own: []))
    }

    func testUnreadableProcessListDegradesOpen() {
        // nil = "could not ask" — NOT evidence the path is idle; targets are
        // review-only so the age gate remains the guard.
        XCTAssertFalse(TmpScanner.pathIsInUse("/tmp/ws", commands: nil, own: []))
    }

    // ── classify: content, never name ──

    func tmpDir(_ name: String) throws -> String {
        let d = NSTemporaryDirectory() + "mktests-\(UUID().uuidString)/\(name)"
        try FileManager.default.createDirectory(atPath: d, withIntermediateDirectories: true)
        addTeardownBlock { try? FileManager.default.removeItem(atPath: (d as NSString).deletingLastPathComponent) }
        return d
    }

    func testBuildPlusIndexAloneIsNotDerivedData() throws {
        // The corroborating-marker rule: Build/ + Index.noindex/ with no
        // Xcode-only marker must NOT classify (an ordinary folder could
        // carry those two names).
        let d = try tmpDir("maybe")
        try FileManager.default.createDirectory(atPath: d + "/Build", withIntermediateDirectories: true)
        try FileManager.default.createDirectory(atPath: d + "/Index.noindex", withIntermediateDirectories: true)
        XCTAssertNil(TmpScanner.classify(d))
        try FileManager.default.createDirectory(atPath: d + "/ModuleCache.noindex", withIntermediateDirectories: true)
        XCTAssertEqual(TmpScanner.classify(d), .derivedData)
    }

    func testFolderMerelyNamedDerivedDataDoesNotClassify() throws {
        let d = try tmpDir("DerivedData")
        XCTAssertNil(TmpScanner.classify(d))
    }

    func testCloneNeedsManifestAndArtifacts() throws {
        let d = try tmpDir("clone")
        try FileManager.default.createDirectory(atPath: d + "/.git", withIntermediateDirectories: true)
        FileManager.default.createFile(atPath: d + "/package.json", contents: Data())
        XCTAssertNil(TmpScanner.classify(d), "manifest without artifacts is a clean checkout")
        try FileManager.default.createDirectory(atPath: d + "/node_modules", withIntermediateDirectories: true)
        XCTAssertEqual(TmpScanner.classify(d), .repoClone)
    }

    // ── slugify parity with Python's re-based implementation ──

    func testSlugifyMatchesPython() {
        XCTAssertEqual(TmpScanner.slugify("ws-derived-top"), "ws-derived-top")
        XCTAssertEqual(TmpScanner.slugify("Foo_Bar.AIS0Ol"), "foo-bar-ais0ol")
        XCTAssertEqual(TmpScanner.slugify("--weird--  name--"), "weird-name")
        XCTAssertEqual(TmpScanner.slugify("release-v0.9.0"), "release-v0-9-0")
    }

    func testDuplicateIdsGetNumericSuffixLikePython() {
        let hits = [TmpHit(path: "/t/foo-bar", kind: .derivedData),
                    TmpHit(path: "/t/foo_bar", kind: .repoClone)]
        let ids = TmpScanner.targets(for: hits).map(\.id)
        XCTAssertEqual(ids, ["tmp-foo-bar", "tmp-foo-bar-2"])
    }
}

import Foundation
import XCTest
@testable import MacCleanerKit

final class ExclusionTests: XCTestCase {
    func testExcludedDescendantWithholdsParentButNotSibling() throws {
        let root = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        defer { try? FileManager.default.removeItem(at: root) }
        let cache = root.appendingPathComponent("cache")
        let cache2 = root.appendingPathComponent("cache2")
        try FileManager.default.createDirectory(at: cache, withIntermediateDirectories: true)
        try FileManager.default.createDirectory(at: cache2, withIntermediateDirectories: true)
        let skip = [cache2.appendingPathComponent("keep").path]
        for pattern: String? in [nil, root.path + "/*"] {
            let target = Target(id: "test", category: "caches", label: "test", desc: "",
                safe: true, emptyOnly: false, paths: [cache.path, cache2.path], glob: pattern, cmd: nil)
            XCTAssertEqual(Scanner.resolvedPaths(target, skip: skip), [cache.path])
        }
        let destination = root.appendingPathComponent("shared")
        try FileManager.default.createDirectory(at: destination, withIntermediateDirectories: true)
        let link = cache.appendingPathComponent("keep")
        try FileManager.default.createSymbolicLink(at: link, withDestinationURL: destination)
        XCTAssertTrue(Scanner.overlapsExclusion(cache.path, skip: Scanner.exclusionPaths([link.path])))
    }
}

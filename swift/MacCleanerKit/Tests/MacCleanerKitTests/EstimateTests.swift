import XCTest
@testable import MacCleanerKit

final class EstimateTests: XCTestCase {
    func testDockerUsesReclaimableColumnAndExcludesVolumes() {
        let output = "TYPE TOTAL ACTIVE SIZE RECLAIMABLE\nImages 2 1 9GB 1GB (11%)\nLocal Volumes 1 0 50GB 50GB (100%)\nBuild Cache 3 1 9MB 2MB (22%)"
        XCTAssertEqual(Estimates.parse(output, parser: "docker_df"), 1075838976)
    }

    func testParsersAndMalformedOutput() {
        XCTAssertEqual(Estimates.parse("free approximately 1.5GB", parser: "brew_dry_run"), 1610612736)
        XCTAssertEqual(Estimates.parse("2 packages (1 GB) and tarballs (2 MB)", parser: "conda_dry_run"), 1075838976)
        XCTAssertEqual(Estimates.parse("12\t/path\n", parser: "du_path"), 12288)
        for parser in ["brew_dry_run", "docker_df", "conda_dry_run", "du_path", "unknown"] {
            XCTAssertEqual(Estimates.parse("unavailable", parser: parser), 0)
            XCTAssertEqual(Estimates.parse("free approximately 1..5GB", parser: parser), 0)
        }
        XCTAssertEqual(Estimates.parse("free approximately " + String(repeating: "9", count: 400) + "GB", parser: "brew_dry_run"), 0)
    }

    func testFailedCommandDiscardsOutput() {
        XCTAssertEqual(Estimates.run("printf '12 cache'; exit 1", parser: "du_path"), 0)
        XCTAssertEqual(Estimates.run("printf '12 cache'", parser: "du_path"), 12288)
    }

    func testTimeoutBoundsShellAndDescendantHoldingOutput() {
        let start = Date()
        XCTAssertEqual(Estimates.run("/bin/sleep 5 & wait", parser: "du_path", timeout: 0.1), 0)
        XCTAssertLessThan(Date().timeIntervalSince(start), 2)
    }
}

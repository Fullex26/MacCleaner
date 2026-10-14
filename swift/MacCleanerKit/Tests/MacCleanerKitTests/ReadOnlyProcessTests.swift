import XCTest
@testable import MacCleanerKit

final class ReadOnlyProcessTests: XCTestCase {
    func testArgumentsArePassedLiterally() {
        let literal = "a path; $(false) 'quoted'"
        XCTAssertEqual(ReadOnlyProcess.run(["%s", literal], executable: "/usr/bin/printf", timeout: 1), literal)
    }

    func testExitFailurePolicyIsExplicit() {
        let arguments = ["-c", "printf partial; exit 7"]
        XCTAssertNil(ReadOnlyProcess.run(arguments, executable: "/bin/sh", timeout: 1))
        XCTAssertEqual(ReadOnlyProcess.run(arguments, executable: "/bin/sh", timeout: 1,
                                          allowFailure: true), "partial")
        XCTAssertNil(ReadOnlyProcess.run([], executable: "/nonexistent-maccleaner-tool", timeout: 1))
    }
}

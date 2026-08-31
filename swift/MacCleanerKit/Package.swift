// swift-tools-version:5.9
// V3 Stage 2 (docs/V3-SWIFT-ENGINE.md): read-only MacCleanerKit.
// Scan/measure/list only — deletion is deliberately absent until Stage 4's
// guard-first port. Verified against the Python engine by
// tools/check_swift_parity.py, which CI runs on every push.
import PackageDescription

let package = Package(
    name: "MacCleanerKit",
    platforms: [.macOS(.v13)],
    targets: [
        .target(name: "MacCleanerKit"),
        .executableTarget(name: "mck", dependencies: ["MacCleanerKit"]),
        .testTarget(name: "MacCleanerKitTests", dependencies: ["MacCleanerKit"]),
    ]
)

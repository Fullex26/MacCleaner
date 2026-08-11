import Foundation

// MARK: - Update manager
//
// Wraps Sparkle's `SPUStandardUpdaterController` behind a small observable
// so Settings (and, later, other views) never touch Sparkle types directly.
// Task 5 ships only the `SPARKLE_DISABLED` stub — the framework isn't
// embedded yet (that's Task 7), but the `import Sparkle` branch below must
// stay syntactically valid Swift so the flag can be dropped later without
// rewriting this file.

#if SPARKLE_DISABLED
final class UpdaterManager: ObservableObject {
    static let shared = UpdaterManager()
    let available = false
    @Published var automaticChecks = false
    func checkForUpdates() {}
}
#else
import Sparkle
final class UpdaterManager: ObservableObject {
    static let shared = UpdaterManager()
    let available = true
    private let controller = SPUStandardUpdaterController(startingUpdater: true,
                                                         updaterDelegate: nil,
                                                         userDriverDelegate: nil)
    @Published var automaticChecks: Bool { didSet { controller.updater.automaticallyChecksForUpdates = automaticChecks } }
    init() { automaticChecks = controller.updater.automaticallyChecksForUpdates }
    func checkForUpdates() { controller.checkForUpdates(nil) }
}
#endif

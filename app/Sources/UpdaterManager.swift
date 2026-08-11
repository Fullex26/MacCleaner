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
    /// Mirrors the Sparkle-enabled build's `canCheck` so SettingsView can
    /// unconditionally write `.disabled(!updater.canCheck)` — always false
    /// here since there's no updater to check with.
    let canCheck = false
    /// Mirrors the Sparkle-enabled build's gentle-reminder signal (B1) so
    /// MenuBarPanel/SettingsView compile and render identically either way
    /// — always nil here, since a stub build never has a pending update.
    /// `@Published` (not a plain `let`) so `CleanerBridge.observeUpdater()`'s
    /// `$pendingUpdateVersion` subscription compiles unconditionally too;
    /// with no Sparkle framework to ever set it, the publisher simply never
    /// fires in this build.
    @Published var pendingUpdateVersion: String?
    func checkForUpdates() {}
}
#else
import Sparkle

/// `NSObject` because `SPUStandardUserDriverDelegate` is an Objective-C
/// protocol Sparkle expects a real `NSObject`-rooted delegate for.
final class UpdaterManager: NSObject, ObservableObject, SPUStandardUserDriverDelegate {
    static let shared = UpdaterManager()
    let available = true

    // `controller` can't be built until after `super.init()` runs — it needs
    // to pass `self` as `userDriverDelegate`, and `self` isn't usable as a
    // full object before `super.init()` returns (two-phase init). Optional
    // (not `!`) so nothing here force-unwraps if some future refactor calls
    // a method before init finishes.
    private var controller: SPUStandardUpdaterController?
    private var canCheckObservation: NSKeyValueObservation?

    @Published var automaticChecks: Bool = false {
        didSet { controller?.updater.automaticallyChecksForUpdates = automaticChecks }
    }

    /// Mirrors `SPUUpdater.canCheckForUpdates` (documented KVO-compliant) —
    /// backs SettingsView's "Check for Updates…" `.disabled(!updater.canCheck)`
    /// so a second check can't be fired while one is already in flight or
    /// data is downloading in the background.
    @Published var canCheck = false

    /// Set when a SCHEDULED (non-user-initiated) update is ready but its
    /// alert would otherwise open with no reliable way for the user to find
    /// it: MacCleaner is `LSUIElement` (no Dock icon, no Cmd-Tab entry), so
    /// Sparkle's own alert for a background check can appear BEHIND every
    /// other window. `supportsGentleScheduledUpdateReminders` + this
    /// delegate callback don't suppress that alert — Sparkle still shows it
    /// gently — this just ALSO surfaces a first-class, discoverable
    /// affordance (a MenuBarPanel row, a Settings row, and a notification)
    /// that calls `checkForUpdates()`, which re-invokes Sparkle as a
    /// user-initiated check and brings the same update to the front.
    /// Cleared once the update session concludes (dismissed, skipped,
    /// installed, or errored — see `standardUserDriverWillFinishUpdateSession`)
    /// or as soon as the user acts on it via `checkForUpdates()`.
    @Published var pendingUpdateVersion: String?

    private override init() {
        super.init()
        let c = SPUStandardUpdaterController(startingUpdater: true,
                                              updaterDelegate: nil,
                                              userDriverDelegate: self)
        controller = c
        automaticChecks = c.updater.automaticallyChecksForUpdates
        canCheck = c.updater.canCheckForUpdates
        canCheckObservation = c.updater.observe(\.canCheckForUpdates, options: [.new]) { [weak self] _, change in
            guard let newValue = change.newValue else { return }
            DispatchQueue.main.async { self?.canCheck = newValue }
        }
    }

    func checkForUpdates() {
        // Immediate feedback: the affordance the user just tapped should
        // disappear right away rather than waiting on Sparkle's session
        // lifecycle to catch up.
        pendingUpdateVersion = nil
        controller?.checkForUpdates(nil)
    }

    // MARK: - SPUStandardUserDriverDelegate (gentle scheduled reminders, B1)

    var supportsGentleScheduledUpdateReminders: Bool { true }

    func standardUserDriverWillHandleShowingUpdate(_ handleShowingUpdate: Bool, forUpdate update: SUAppcastItem, state: SPUUserUpdateState) {
        // User-initiated checks (Settings' "Check for Updates…" or the
        // MenuBarPanel/Settings row calling checkForUpdates()) are always
        // handled by Sparkle's standard, fully-visible UI — nothing extra to
        // surface there; this is only for checks the user never asked for.
        guard !state.userInitiated else { return }
        let version = update.displayVersionString
        DispatchQueue.main.async { [weak self] in
            self?.pendingUpdateVersion = version
        }
    }

    func standardUserDriverWillFinishUpdateSession() {
        DispatchQueue.main.async { [weak self] in
            self?.pendingUpdateVersion = nil
        }
    }
}
#endif

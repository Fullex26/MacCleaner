import Foundation
import UserNotifications

/// Posts native notifications, degrading silently when unavailable.
///
/// An ad-hoc-signed app may fail to register with the notification centre, and
/// the user may simply deny permission. Neither is an error worth surfacing:
/// the app must never block or complain because a notification could not be
/// delivered. The CLI's osascript path covers the headless case.
///
/// `NotificationManager` is its own `UNUserNotificationCenterDelegate` because
/// UNUserNotificationCenter's documented default is to *suppress* a
/// notification that's posted while the app is frontmost, unless a delegate
/// implements `willPresent` and opts back in. The app's main trigger — hitting
/// Clean in the open Dashboard window — is exactly that frontmost case, so
/// without this delegate the feature would silently do nothing on its most
/// common path (it would still work for background/scheduled cleans). Do not
/// remove this as unused: it's what makes foreground notifications appear.
final class NotificationManager: NSObject, UNUserNotificationCenterDelegate {
    static let shared = NotificationManager()
    private var authorized = false

    private override init() {}

    func requestAuthorization() {
        // Assign the delegate before requesting authorization so it's in
        // place no matter how the user responds to the permission prompt
        // (including denial) — nothing here depends on `authorized`.
        UNUserNotificationCenter.current().delegate = self
        UNUserNotificationCenter.current()
            .requestAuthorization(options: [.alert, .sound]) { granted, _ in
                DispatchQueue.main.async { self.authorized = granted }
            }
    }

    func post(title: String, body: String) {
        guard authorized else { return }
        let content = UNMutableNotificationContent()
        content.title = title
        content.body = body
        let request = UNNotificationRequest(identifier: UUID().uuidString,
                                            content: content, trigger: nil)
        UNUserNotificationCenter.current().add(request, withCompletionHandler: nil)
    }

    // Invoked by the system instead of the default (silent) behavior when a
    // notification would be delivered while MacCleaner is frontmost. Opting
    // into banner + sound here is what makes Clean's completion notification
    // visible even though the Dashboard window is the one that triggered it.
    func userNotificationCenter(
        _ center: UNUserNotificationCenter,
        willPresent notification: UNNotification,
        withCompletionHandler completionHandler: @escaping (UNNotificationPresentationOptions) -> Void
    ) {
        completionHandler([.banner, .sound])
    }
}

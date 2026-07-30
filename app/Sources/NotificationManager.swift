import Foundation
import UserNotifications

/// Posts native notifications, degrading silently when unavailable.
///
/// An ad-hoc-signed app may fail to register with the notification centre, and
/// the user may simply deny permission. Neither is an error worth surfacing:
/// the app must never block or complain because a notification could not be
/// delivered. The CLI's osascript path covers the headless case.
final class NotificationManager {
    static let shared = NotificationManager()
    private var authorized = false

    private init() {}

    func requestAuthorization() {
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
}

#!/usr/bin/env python3
"""Capture notification text from actual bridge cleanup calls and a fake engine."""
import os
from pathlib import Path
import signal
import subprocess
import tempfile

REPO = Path(__file__).resolve().parents[1]
ENGINE = r'''
import json, os, sys
from pathlib import Path
command = sys.argv[1]
if command == 'clean':
    statuses = json.loads(Path(os.environ['MACCLEANER_NOTIFICATION_FIXTURE']).read_text())
    result = {'freed_bytes': 4096, 'freed_human': '4 KB', 'items': [
        {'id': str(i), 'label': str(i), 'freed': 0, 'status': status,
         'error': 'fixture failure' if status == 'error' else None}
        for i, status in enumerate(statuses)]}
elif command == 'scan':
    result = {'timestamp': '2026-10-15T00:00:00', 'disk': 'fixture',
              'total_reclaimable_bytes': 0, 'total_reclaimable_human': '0 B', 'targets': []}
elif command == 'config':
    result = {'notifications': True, 'v3_soak': False, 'low_disk_alerts': False}
elif command == 'categories':
    result = {'categories': []}
elif command == 'report':
    result = {'runs': []}
else:
    result = {}
print(json.dumps(result))
'''
PROBE = r'''
import Foundation
import AppKit
final class NotificationManager {
    static let shared = NotificationManager()
    var messages: [(String, String)] = []
    func post(title: String, body: String) { messages.append((title, body)) }
}
@main
struct NotificationProbe {
    @MainActor
    static func main() async {
        _ = NSApplication.shared
        let bridge = CleanerBridge()
        let fixture = URL(fileURLWithPath: ProcessInfo.processInfo.environment["MACCLEANER_NOTIFICATION_FIXTURE"]!)
        let cases: [([String], [String])] = [
            (["error"], ["0 items cleaned", "1 failed"]),
            (["deleted", "trashed", "error", "skipped"], ["2 items cleaned", "1 failed", "1 skipped"]),
            (["skipped"], ["0 items cleaned", "1 skipped"]),
            (["deleted"], ["1 item cleaned"])
        ]
        var failed = false
        for automatic in [false, true] {
            for (statuses, expected) in cases {
                try! JSONSerialization.data(withJSONObject: statuses).write(to: fixture)
                NotificationManager.shared.messages.removeAll()
                if automatic { await bridge.autoCleanSafe() }
                else { await bridge.clean(ids: ["fixture"]) }
                let messages = NotificationManager.shared.messages
                let valid = messages.count == 1 && messages[0].0 == "MacCleaner freed 4 KB"
                    && messages[0].1 == expected.joined(separator: " · ")
                print("\(valid ? "ok" : "FAIL") automatic=\(automatic) statuses=\(statuses) messages=\(messages)")
                if !valid { failed = true }
            }
        }
        exit(failed ? 1 : 0)
    }
}
'''


def main():
    with tempfile.TemporaryDirectory(prefix='mc-notification-') as td:
        root = Path(td)
        source, engine, binary = root / 'Probe.swift', root / 'engine.py', root / 'probe'
        source.write_text(PROBE)
        engine.write_text(ENGINE)
        subprocess.run(['swiftc', '-swift-version', '5', '-DSPARKLE_DISABLED',
                        str(REPO / 'app/Sources/CleanerBridge.swift'),
                        str(REPO / 'app/Sources/UpdaterManager.swift'), str(source), '-o', str(binary)],
                       check=True, timeout=120)
        process = subprocess.Popen([str(binary)], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                   text=True, start_new_session=True,
                                   env={**os.environ, 'MACCLEANER_ENGINE': str(engine),
                                        'MACCLEANER_NOTIFICATION_FIXTURE': str(root / 'statuses.json')})
        try:
            out, err = process.communicate(timeout=45)
            print(out, end='')
            if process.returncode:
                raise SystemExit(err or 'cleanup notification probe failed')
        finally:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.communicate(timeout=3)


if __name__ == '__main__':
    main()

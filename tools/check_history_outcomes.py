#!/usr/bin/env python3
"""Reload historical outcomes through the production bridge using fixture records."""
import os
from pathlib import Path
import signal
import subprocess
import tempfile

REPO = Path(__file__).resolve().parent.parent
ENGINE = r'''
import json, os, sys
from pathlib import Path
command = sys.argv[1:]
if command == ['categories', '--json']: result = {'categories': []}
elif command == ['config', 'show']: result = {'notifications': False, 'v3_soak': False}
elif command[0] == 'report':
    result = json.loads((Path(os.environ['MACCLEANER_REQUEST_FIXTURES']) / 'history.json').read_text())
else: raise SystemExit('unexpected invocation: ' + str(command))
print(json.dumps(result))
'''
PROBE = r'''
import Foundation
import AppKit

@main
struct HistoryProbe {
    @MainActor
    static func main() async throws {
        _ = NSApplication.shared
        let root = URL(fileURLWithPath: ProcessInfo.processInfo.environment["MACCLEANER_REQUEST_FIXTURES"]!)
        let fixture: [String: Any] = ["runs": [
            ["timestamp": "2026-10-23T10:00:00", "total_freed_bytes": 4096, "total_freed_human": "4 KB", "disk_after": "fixture",
             "items": [["id": "failure", "label": "Partial failure", "freed": 4096, "status": "error", "error": "permission denied"]]],
            ["timestamp": "2026-10-23T11:00:00", "total_freed_bytes": 8192, "total_freed_human": "8 KB", "disk_after": "fixture",
             "items": ["deleted", "trashed", "skipped", "error", "future-status"].map {
                 ["id": "repeated-id", "label": $0, "freed": 0, "status": $0] as [String: Any]
             }],
            ["timestamp": "2026-10-23T12:00:00", "total_freed_bytes": 2048, "total_freed_human": "2 KB", "disk_after": "fixture"],
            ["timestamp": "2026-10-23T13:00:00", "total_freed_bytes": 0, "total_freed_human": "0 B", "disk_after": "fixture", "items": "legacy malformed"]
        ]]
        try JSONSerialization.data(withJSONObject: fixture).write(to: root.appendingPathComponent("history.json"))
        for _ in 0..<2 {
            let bridge = CleanerBridge()
            await bridge.ensureSettingsLoaded().value
            await bridge.loadHistory()
            guard bridge.history.count == 4 else { fatalError("history lost runs") }
            let history = Array(bridge.history.reversed())
            let failed = history[0]
            guard failed.outcomeSummary == "1 failed", failed.total_freed_bytes == 4096,
                  failed.items[0].freed == 4096, failed.items[0].error == "permission denied",
                  failed.items[0].outcomeLabel == "Failed" else { fatalError("partial error represented as success") }
            guard history[1].items.count == 5, Set(history[1].items.map(\.id)).count == 1 else { fatalError("duplicate IDs lost records") }
            guard history[1].outcomeSummary == "1 deleted · 1 trashed · 1 skipped · 1 failed · 1 unknown",
                  history[1].items.last?.outcomeLabel == "Unknown (future-status)" else {
                fatalError("mixed outcomes lost")
            }
            guard history[2].outcomeSummary == "Item details unavailable",
                  history[2].total_freed_bytes == 2048,
                  history[3].outcomeSummary == "Item details unavailable" else {
                fatalError("legacy details invented or totals lost")
            }
        }
        print("ok   error-only, partial success, mixed and unknown outcomes survive bridge reload")
        print("ok   legacy missing/malformed item details preserve run totals")
    }
}
'''

def main():
    with tempfile.TemporaryDirectory(prefix="mc-history-outcomes-") as td:
        root = Path(td)
        source, engine, executable = root / "Probe.swift", root / "engine.py", root / "probe"
        source.write_text(PROBE)
        engine.write_text(ENGINE)
        sources = [REPO / "app/Sources" / name for name in
                   ("CleanerBridge.swift", "NotificationManager.swift", "UpdaterManager.swift")]
        subprocess.run(["swiftc", "-swift-version", "5", "-DSPARKLE_DISABLED",
                        *map(str, sources), str(source), "-o", str(executable)],
                       check=True, timeout=120)
        process = subprocess.Popen(
            [str(executable)], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            start_new_session=True,
            env={**os.environ, "MACCLEANER_ENGINE": str(engine),
                 "MACCLEANER_REQUEST_FIXTURES": str(root)})
        try:
            out, err = process.communicate(timeout=30)
            print(out, end="")
            if process.returncode:
                raise SystemExit(err or "History outcome probe failed")
        finally:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.communicate(timeout=3)


if __name__ == "__main__":
    main()

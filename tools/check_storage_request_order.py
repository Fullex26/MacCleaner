#!/usr/bin/env python3
"""Exercise the actual Swift bridge with a controlled, temporary JSON engine."""
import os
from pathlib import Path
import signal
import subprocess
import tempfile

REPO = Path(__file__).resolve().parent.parent
ENGINE = r'''
import json
import os
from pathlib import Path
import sys
import time

if sys.argv[1] != "storage-map":
    print("{}")
    raise SystemExit(0)
name = sys.argv[2].lstrip("/")
root = Path(os.environ["MACCLEANER_REQUEST_FIXTURES"])
(root / (name + ".started")).touch()
deadline = time.monotonic() + 10
while not (root / (name + ".release")).exists():
    if time.monotonic() >= deadline:
        raise SystemExit("fixture release deadline exceeded")
    time.sleep(0.01)
if name.endswith("failure"):
    raise SystemExit("fixture measurement failure")
print(json.dumps({"root": "/" + name, "total_bytes": 0, "total_human": "0 B",
                  "category": "other", "children": []}))
'''
PROBE = r'''
import Foundation

@main
struct StorageRequestProbe {
    @MainActor
    static func main() async {
        let bridge = CleanerBridge()
        let root = URL(fileURLWithPath: ProcessInfo.processInfo.environment["MACCLEANER_REQUEST_FIXTURES"]!)
        var failed = false
        func check(_ condition: Bool, _ message: String) {
            print("\(condition ? "ok  " : "FAIL") \(message)")
            if !condition { failed = true }
        }
        func waitStarted(_ name: String) async throws {
            let deadline = ProcessInfo.processInfo.systemUptime + 5
            while !FileManager.default.fileExists(atPath: root.appendingPathComponent(name + ".started").path) {
                if ProcessInfo.processInfo.systemUptime >= deadline {
                    throw NSError(domain: "StorageRequestProbe", code: 1,
                                  userInfo: [NSLocalizedDescriptionKey: "request did not start: \(name)"])
                }
                try await Task.sleep(nanoseconds: 10_000_000)
            }
        }
        func release(_ name: String) throws {
            try Data().write(to: root.appendingPathComponent(name + ".release"))
        }
        func runCase(_ old: String, _ new: String, oldFirst: Bool) async throws {
            let priorRoot = bridge.storageMap?.root
            let priorError = bridge.storageMapError
            let older = Task { await bridge.scanStorageMap("/" + old) }
            try await waitStarted(old)
            let newer = Task { await bridge.scanStorageMap("/" + new) }
            try await waitStarted(new)
            if oldFirst {
                try release(old)
                await older.value
                check(bridge.isScanningStorageMap, "older completion cannot clear newer busy state")
                check(bridge.storageMap?.root == priorRoot && bridge.storageMapError == priorError,
                      "older completion cannot publish while newer request is pending")
                try release(new)
                await newer.value
            } else {
                try release(new)
                await newer.value
                check(!bridge.isScanningStorageMap, "latest completion clears busy state")
                try release(old)
                await older.value
            }
            if new.hasSuffix("failure") {
                check(bridge.storageMap?.root == priorRoot && bridge.storageMapError != nil,
                      "stale success cannot replace latest error")
            } else {
                check(bridge.storageMap?.root == "/" + new && bridge.storageMapError == nil,
                      "latest destination survives stale completion: \(new)")
            }
            check(!bridge.isScanningStorageMap, "finished latest request is idle")
        }
        do {
            try await runCase("old-success-a", "new-success-a", oldFirst: false)
            try await runCase("old-failure", "new-success-b", oldFirst: false)
            try await runCase("old-success-c", "new-success-c", oldFirst: true)
            try await runCase("old-success-d", "new-failure", oldFirst: false)
        } catch {
            print("FAIL Storage request fixture: \(error)")
            failed = true
        }
        exit(failed ? 1 : 0)
    }
}
'''


def main():
    with tempfile.TemporaryDirectory(prefix="mc-storage-order-") as td:
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
                raise SystemExit(err or "Storage request ordering probe failed")
        finally:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.communicate(timeout=3)


if __name__ == "__main__":
    main()

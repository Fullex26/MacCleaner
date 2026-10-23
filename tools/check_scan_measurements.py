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

root = Path(os.environ["MACCLEANER_REQUEST_FIXTURES"])
with (root / "calls").open("a") as log:
    log.write(sys.argv[1] + "\n")
assert sys.argv[1:] == ["scan", "--json"], sys.argv
counter = root / "count"
n = int(counter.read_text()) + 1 if counter.exists() else 1
counter.write_text(str(n))
if n == 2:
    raise SystemExit("fixture retry failed")
target = {"id": "fixture-cache", "category": "caches", "label": "Fixture cache",
          "description": "Fixture", "size_bytes": 4096, "size_human": "4 KB",
          "safe": True, "exists": True}
if n == 1:
    target.update(size_bytes=0, size_human="0 B", exists=False,
                  measurement_error="directory enumeration timed out")
print(json.dumps({"timestamp": str(n), "disk": "fixture", "total_reclaimable_bytes": target["size_bytes"],
                  "total_reclaimable_human": target["size_human"], "targets": [target]}))
'''
PROBE = r'''
import Foundation

@main
struct MeasurementProbe {
    @MainActor
    static func main() async throws {
        let bridge = CleanerBridge()
        bridge.v3SoakEnabled = false
        await bridge.scan()
        let unavailableReport = bridge.report
        guard let failed = bridge.report?.targets.first else { fatalError("missing fixture target") }
        let encoded = try JSONSerialization.jsonObject(with: JSONEncoder().encode(failed)) as! [String: Any]
        guard encoded["measurement_error"] as? String == "directory enumeration timed out" else {
            print("FAIL real bridge discarded unavailable measurement diagnostic")
            exit(1)
        }
        guard bridge.report?.dashboardTargets.map(\.id) == ["fixture-cache"],
              !failed.canSelectForCleanup else { fatalError("unavailable target was hidden or selectable") }
        var inconsistent = encoded
        inconsistent["exists"] = true
        inconsistent["size_bytes"] = 4096
        let unknown = try JSONDecoder().decode(ScanTarget.self, from: JSONSerialization.data(withJSONObject: inconsistent))
        guard !unknown.canSelectForCleanup else { fatalError("error with stale size became selectable") }
        print("ok   unavailable measurements stay visible and cannot be selected")
        await bridge.scan()
        guard bridge.report?.targets.first?.measurement_error != nil, bridge.statusMessage != nil else {
            fatalError("failed retry erased the unavailable measurement")
        }
        print("ok   failed retry retains diagnostic")
        await bridge.scan()
        guard let retried = bridge.report?.targets.first, retried.exists == true, retried.size_bytes == 4096 else {
            fatalError("successful retry did not replace unavailable result")
        }
        let recovered = try JSONSerialization.jsonObject(with: JSONEncoder().encode(retried)) as! [String: Any]
        guard retried.canSelectForCleanup, bridge.report?.dashboardTargets.count == 1, recovered["measurement_error"] == nil else { fatalError("retry retained stale diagnostic") }
        print("ok   successful legacy-shaped retry clears warning and restores size")
        var absent = recovered
        absent["exists"] = false
        absent["size_bytes"] = 0
        let missing = try JSONDecoder().decode(ScanTarget.self, from: JSONSerialization.data(withJSONObject: absent))
        guard !missing.isVisibleOnDashboard, !missing.canSelectForCleanup else {
            fatalError("genuinely absent target appeared as unavailable")
        }
        print("ok   legacy absent targets remain hidden")
        let frozenIDs = [retried.id]
        bridge.report = unavailableReport
        let calls = URL(fileURLWithPath: ProcessInfo.processInfo.environment["MACCLEANER_REQUEST_FIXTURES"]!).appendingPathComponent("calls")
        let before = try String(contentsOf: calls, encoding: .utf8)
        await bridge.clean(ids: frozenIDs)
        guard try String(contentsOf: calls, encoding: .utf8) == before, bridge.lastCleanFailed != nil else {
            print("FAIL stale confirmation reached the engine after measurement became unavailable")
            exit(1)
        }
        print("ok   changed eligibility blocks stale confirmation before engine execution")
    }
}
'''


def main():
    with tempfile.TemporaryDirectory(prefix="mc-scan-measurements-") as td:
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
                raise SystemExit(err or "Scan measurement probe failed")
        finally:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.communicate(timeout=3)


if __name__ == "__main__":
    main()

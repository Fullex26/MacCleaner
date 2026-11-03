#!/usr/bin/env python3
"""Exercise the actual Swift bridge with an engine that warns while succeeding.

`runEngineOutput` has always captured stderr, but `runEngine` discards it, so
every caller except `previewCleanup` loses a warning the engine wrote while
still exiting 0 — partial project discovery, a truncated storage walk, a
corrupt config falling back to defaults. The CLI user reads those on stderr;
the app user never saw them (R06).

The warning is diagnostic only. It must never read as a failure, and it must
never widen what may be cleaned.
"""
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
sub = sys.argv[1]
with (root / "calls").open("a") as log:
    log.write(" ".join(sys.argv[1:]) + "\n")
counter = root / ("count-" + sub)
n = int(counter.read_text()) + 1 if counter.exists() else 1
counter.write_text(str(n))

TARGET = {"id": "fixture-cache", "category": "caches", "label": "Fixture cache",
          "description": "Fixture", "size_bytes": 4096, "size_human": "4 KB",
          "safe": True, "exists": True}

if sub == "scan":
    # Calls 1 and 3 warn; call 2 is quiet, so the probe can prove the notice
    # is cleared by an ordinary success rather than merely overwritten.
    if n != 2:
        print("Warning: project discovery stopped after 120s; results are partial",
              file=sys.stderr)
    print(json.dumps({"timestamp": str(n), "disk": "fixture",
                      "total_reclaimable_bytes": 4096, "total_reclaimable_human": "4 KB",
                      "targets": [TARGET]}))
elif sub == "clean":
    print("Warning: could not write snapshot: fixture is read-only", file=sys.stderr)
    print(json.dumps({"delete_mode": "rm", "freed_bytes": 4096, "freed_human": "4 KB",
                      "disk_after": "fixture",
                      "items": [{"id": "fixture-cache", "label": "Fixture cache",
                                 "freed": 4096, "status": "deleted", "error": None,
                                 "freed_unknown": None}]}))
else:
    print("{}")
'''
PROBE = r'''
import Foundation

@main
struct DiagnosticsProbe {
    @MainActor
    static func main() async throws {
        let bridge = CleanerBridge()
        bridge.v3SoakEnabled = false

        // 1. A warning written while the engine still exits 0 reaches the app.
        await bridge.scan()
        guard let notice = bridge.engineNotices["scan"] else {
            print("FAIL real bridge dropped a warning from a successful scan")
            exit(1)
        }
        guard notice.text.contains("results are partial") else {
            fatalError("scan notice lost the engine's text: \(notice.text)")
        }
        guard bridge.report?.targets.count == 1, bridge.statusMessage == nil,
              bridge.lastCleanFailed == nil else {
            fatalError("a warning was treated as a failed scan")
        }
        print("ok   a warning on a successful scan reaches the app")

        // 2. Ordinary success stays quiet — the notice is cleared, not sticky.
        await bridge.scan()
        guard bridge.engineNotices["scan"] == nil else {
            print("FAIL a quiet scan left the previous warning on screen")
            exit(1)
        }
        print("ok   an ordinary success clears the notice")

        // 3. A notice is diagnostic: it must not widen what may be cleaned.
        await bridge.scan()
        guard bridge.engineNotices["scan"] != nil else { fatalError("third scan did not warn") }
        guard let target = bridge.report?.targets.first else { fatalError("missing fixture target") }
        let selectable = bridge.report?.dashboardTargets.filter(\.canSelectForCleanup).map(\.id)
        guard selectable == ["fixture-cache"], target.canSelectForCleanup else {
            fatalError("notice changed cleanup eligibility")
        }
        print("ok   a notice does not change cleanup eligibility")

        // 4. A clean that warns is still a clean, not a failure.
        await bridge.clean(ids: ["fixture-cache"])
        guard let cleanNotice = bridge.engineNotices["clean"] else {
            print("FAIL real bridge dropped a warning from a successful clean")
            exit(1)
        }
        guard cleanNotice.text.contains("could not write snapshot") else {
            fatalError("clean notice lost the engine's text: \(cleanNotice.text)")
        }
        guard bridge.lastCleanFailed == nil, bridge.lastClean?.freed_bytes == 4096 else {
            fatalError("a warning turned a successful clean into a failure")
        }
        print("ok   a warning on a successful clean is not reported as a failure")
    }
}
'''


def main():
    with tempfile.TemporaryDirectory(prefix="mc-engine-diagnostics-") as td:
        root = Path(td)
        source, engine, executable = root / "Probe.swift", root / "engine.py", root / "probe"
        source.write_text(PROBE)
        engine.write_text(ENGINE)
        sources = [REPO / "app/Sources" / name for name in
                   ("CleanerBridge.swift", "NotificationManager.swift", "UpdaterManager.swift")]
        subprocess.run(["swiftc", "-swift-version", "5", "-DSPARKLE_DISABLED",
                        *map(str, sources), str(source), "-o", str(executable)],
                       check=True, timeout=180)
        process = subprocess.Popen(
            [str(executable)], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            start_new_session=True,
            env={**os.environ, "MACCLEANER_ENGINE": str(engine),
                 "MACCLEANER_REQUEST_FIXTURES": str(root)})
        try:
            out, err = process.communicate(timeout=60)
            print(out, end="")
            if process.returncode:
                raise SystemExit(err or "Engine diagnostics probe failed")
        finally:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.communicate(timeout=3)


if __name__ == "__main__":
    main()

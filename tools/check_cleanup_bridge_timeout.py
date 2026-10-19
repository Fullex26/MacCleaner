#!/usr/bin/env python3
"""Actual bridge timeout against harmless engine-owned cleanup descendants."""
import json
import os
from pathlib import Path
import signal
import subprocess
import tempfile

REPO = Path(__file__).resolve().parents[1]
ENGINE = r'''
import os, shlex, signal, sys, time
from pathlib import Path
sys.path.insert(0, os.environ["MACCLEANER_FIXTURE_REPO"])
import cleaner
if sys.argv[1] == "ignore-term":
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
    time.sleep(20)
    raise SystemExit(0)
root = Path(os.environ["MACCLEANER_CLEANUP_FIXTURE"])
child = (
    "import json,os,time; from pathlib import Path; "
    f"root=Path({str(root)!r}); record=root/'pid.json'; temporary=root/'pid.ready'; "
    "temporary.write_text(json.dumps([os.getpid(),os.getpgrp()])); temporary.replace(record); "
    "time.sleep(1.5); (root/'late-write').write_text('continued'); time.sleep(10)"
)
cleaner.delete_target({"cmd": shlex.quote(sys.executable) + " -c " + shlex.quote(child) + " & wait"})
'''
PROBE = r'''
import Foundation
@main
struct CleanupTimeoutProbe {
    static func fail(_ message: String) -> Never {
        fputs(message + "\n", stderr)
        exit(1)
    }
    static func main() {
        let root = URL(fileURLWithPath: ProcessInfo.processInfo.environment["MACCLEANER_CLEANUP_FIXTURE"]!)
        let started = ProcessInfo.processInfo.systemUptime
        do {
            _ = try CleanerBridge.runEngine(["fixture"], timeout: 0.75)
            fail("expected bridge timeout")
        } catch {
            guard error.localizedDescription.contains("gave up") else { fail("unexpected error: \(error)") }
        }
        guard ProcessInfo.processInfo.systemUptime - started < 4 else { fail("shutdown was not bounded") }
        Thread.sleep(forTimeInterval: 1.8)
        guard FileManager.default.fileExists(atPath: root.appendingPathComponent("pid.json").path) else {
            fail("fixture child did not start")
        }
        guard !FileManager.default.fileExists(atPath: root.appendingPathComponent("late-write").path) else {
            fail("cleanup descendant continued after bridge timeout")
        }
        print("ok   bridge timeout stops engine-owned cleanup")
        let hardStopStarted = ProcessInfo.processInfo.systemUptime
        do {
            _ = try CleanerBridge.runEngine(["ignore-term"], timeout: 0.75)
            fail("expected hard-stop timeout")
        } catch {
            guard error.localizedDescription.contains("gave up") else { fail("unexpected hard-stop error") }
        }
        guard ProcessInfo.processInfo.systemUptime - hardStopStarted < 4 else {
            fail("TERM-ignoring engine exceeded hard-stop bound")
        }
        print("ok   hard stop bounds TERM-ignoring engine")
    }
}
'''


def main():
    with tempfile.TemporaryDirectory(prefix="mc-cleanup-bridge-") as td:
        root = Path(td)
        source, engine, executable = root / 'Probe.swift', root / 'engine.py', root / 'probe'
        source.write_text(PROBE)
        engine.write_text(ENGINE)
        sources = [REPO / 'app/Sources' / name for name in
                   ('CleanerBridge.swift', 'NotificationManager.swift', 'UpdaterManager.swift')]
        subprocess.run(['swiftc', '-swift-version', '5', '-DSPARKLE_DISABLED',
                        *map(str, sources), str(source), '-o', str(executable)], check=True, timeout=120)
        process = subprocess.Popen([str(executable)], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                   text=True, start_new_session=True,
                                   env={**os.environ, 'MACCLEANER_ENGINE': str(engine),
                                        'MACCLEANER_FIXTURE_REPO': str(REPO),
                                        'MACCLEANER_CLEANUP_FIXTURE': str(root)})
        try:
            out, err = process.communicate(timeout=15)
            print(out, end='')
            if process.returncode:
                raise SystemExit(err or 'cleanup bridge probe failed')
        finally:
            groups = {process.pid}
            try:
                groups.add(json.loads((root / 'pid.json').read_text())[1])
            except (OSError, ValueError, IndexError):
                pass
            for group in groups:
                try:
                    os.killpg(group, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            process.communicate(timeout=3)


if __name__ == '__main__':
    main()

#!/usr/bin/env python3
"""R09: drive the real bridge's V3 soak through every state with a fake
engine and a fake mck. Only a completed comparison may read as agreement;
a missing helper, a missing config, a failed or timed-out run must each be
reported as exactly that. mck must be handed the config the Python engine
actually used, not a hardcoded ~/mac-cleaner path."""
import os
from pathlib import Path
import signal
import stat
import subprocess
import tempfile

REPO = Path(__file__).resolve().parent.parent
ENGINE = r'''
import json, os, sys
from pathlib import Path
root = Path(os.environ["MC_SOAK_FIXTURES"])
if sys.argv[1:] == ["config", "path"]:
    print((root / "configpath").read_text())
    raise SystemExit(0)
assert sys.argv[1:] == ["scan", "--json"], sys.argv
print(json.dumps({"timestamp": "t", "disk": "fixture", "total_reclaimable_bytes": 4096,
                  "total_reclaimable_human": "4 KB", "targets": [
    {"id": "fixture-cache", "category": "caches", "label": "Fixture", "description": "",
     "size_bytes": 4096, "size_human": "4 KB", "safe": True, "exists": True}]}))
'''
MCK = r'''#!/usr/bin/env python3
import json, os, time
from pathlib import Path
root = Path(os.environ["MC_SOAK_FIXTURES"])
(root / "mck-config").write_text(os.environ.get("MACCLEANER_CONFIG", ""))
mode = (root / "mode").read_text().strip()
if mode == "exit3":
    raise SystemExit(3)
if mode == "garbage":
    print("not json")
    raise SystemExit(0)
if mode == "hang":
    time.sleep(30)
safe = mode != "diverge"
print(json.dumps({"targets": [{"id": "fixture-cache", "safe": safe, "exists": True,
                               "size_bytes": 4096, "cmd": False}]}))
'''
PROBE = r'''
import Foundation

@main
struct SoakProbe {
    @MainActor
    static func main() async throws {
        let env = ProcessInfo.processInfo.environment
        let root = URL(fileURLWithPath: env["MC_SOAK_FIXTURES"]!)
        let mck = env["MACCLEANER_MCK"]!
        let config = root.appendingPathComponent("engine-config.json").path
        var failed = false
        func check(_ ok: Bool, _ name: String) {
            print("\(ok ? "ok  " : "FAIL") \(name)")
            if !ok { failed = true }
        }
        func write(_ name: String, _ text: String) throws {
            try text.write(to: root.appendingPathComponent(name), atomically: true, encoding: .utf8)
        }
        func soak(_ bridge: CleanerBridge, mode: String) async throws -> SoakState {
            try write("mode", mode)
            await bridge.scan()
            await bridge.soakTask?.value
            return bridge.soakState
        }

        let bridge = CleanerBridge()
        try write("configpath", config)
        try "{}".write(toFile: config, atomically: true, encoding: .utf8)

        // Agreement, and mck is given the engine's own config path.
        var state = try await soak(bridge, mode: "agree")
        if case let .compared(n, used, _) = state {
            check(n == 0 && used == config, "agreeing engines report a completed comparison with 0 divergences")
        } else { check(false, "agreeing engines must complete a comparison, got \(state)") }
        let handed = try String(contentsOf: root.appendingPathComponent("mck-config"), encoding: .utf8)
        check(handed == config, "mck reads the config the Python engine used (\(handed))")
        check(bridge.soakDivergences == 0, "divergence count is published for a real comparison")

        state = try await soak(bridge, mode: "diverge")
        if case let .compared(n, _, _) = state { check(n > 0, "a safe-flag mismatch is counted") }
        else { check(false, "diverging engines still complete a comparison: \(state)") }

        for (mode, name) in [("exit3", "non-zero exit"), ("garbage", "unparseable output")] {
            state = try await soak(bridge, mode: mode)
            if case .failed = state { check(bridge.soakDivergences == nil, "\(name) is a failure, never agreement") }
            else { check(false, "\(name) must be .failed, got \(state)") }
        }

        state = try await soak(bridge, mode: "hang")
        if case let .failed(reason, _) = state { check(reason.contains("didn't finish"), "a hung mck is reported as timed out") }
        else { check(false, "hung mck must be .failed, got \(state)") }

        // The engine has no config file: never compare against defaults.
        try write("configpath", root.appendingPathComponent("absent.json").path)
        state = try await soak(bridge, mode: "agree")
        if case .unavailable = state { check(bridge.soakDivergences == nil, "missing engine config is unavailable, not agreement") }
        else { check(false, "missing config must be .unavailable, got \(state)") }
        try write("configpath", config)

        // mck not executable: unavailable, and the previous result is not kept.
        _ = try await soak(bridge, mode: "agree")
        try FileManager.default.setAttributes([.posixPermissions: 0o644], ofItemAtPath: mck)
        state = try await soak(bridge, mode: "agree")
        if case .unavailable = state { check(bridge.soakDivergences == nil, "missing mck clears the last agreement") }
        else { check(false, "missing mck must be .unavailable, got \(state)") }
        try FileManager.default.setAttributes([.posixPermissions: 0o755], ofItemAtPath: mck)

        bridge.v3SoakEnabled = false
        state = try await soak(bridge, mode: "agree")
        check(state == .disabled, "disabled soak reports disabled and runs nothing")

        exit(failed ? 1 : 0)
    }
}
'''


def main():
    with tempfile.TemporaryDirectory(prefix="mc-soak-") as td:
        root = Path(td)
        source, engine, mck, executable = root / "Probe.swift", root / "engine.py", root / "mck", root / "probe"
        source.write_text(PROBE)
        engine.write_text(ENGINE)
        mck.write_text(MCK)
        mck.chmod(mck.stat().st_mode | stat.S_IXUSR)
        sources = [REPO / "app/Sources" / name for name in
                   ("CleanerBridge.swift", "NotificationManager.swift", "UpdaterManager.swift")]
        subprocess.run(["swiftc", "-swift-version", "5", "-DSPARKLE_DISABLED",
                        *map(str, sources), str(source), "-o", str(executable)],
                       check=True, timeout=180)
        process = subprocess.Popen(
            [str(executable)], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            start_new_session=True,
            env={**os.environ, "MACCLEANER_ENGINE": str(engine), "MACCLEANER_MCK": str(mck),
                 "MC_SOAK_FIXTURES": str(root), "MACCLEANER_SOAK_TIMEOUT": "2",
                 "MACCLEANER_SOAK_LOG": str(root / "soak.log")})
        try:
            out, err = process.communicate(timeout=120)
            print(out, end="")
            if process.returncode:
                raise SystemExit(err or "Soak state probe failed")
        finally:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.communicate(timeout=3)


if __name__ == "__main__":
    main()

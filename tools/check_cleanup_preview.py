#!/usr/bin/env python3
"""Exercise cleanup previews through the production Swift bridge with fixture data."""
import os
from pathlib import Path
import signal
import subprocess
import tempfile

REPO = Path(__file__).resolve().parent.parent
ENGINE = r'''
import json, os, sys
from pathlib import Path
root = Path(os.environ['MACCLEANER_REQUEST_FIXTURES'])
args = sys.argv[1:]
if args == ['categories', '--json']:
    print(json.dumps({'categories': []}))
    raise SystemExit(0)
if args == ['config', 'show']:
    print(json.dumps({'delete_mode': 'rm', 'notifications': False}))
    raise SystemExit(0)
with (root / 'calls').open('a') as log: log.write(json.dumps(args) + '\n')
assert args[:2] == ['clean', '--targets'] and '--yes' in args and '--dry-run' in args and '--json' in args, args
scenario = (root / 'scenario').read_text()
items = [{'id': 'path', 'label': 'Path fixture', 'status': 'would-delete', 'paths': [{'path': '/fixture/cache', 'size_bytes': 4096}]},
         {'id': 'command', 'label': 'Command fixture', 'status': 'would-run', 'cmd': 'fixture cleanup', 'paths': []}]
mode = 'trash' if '--trash' in args else 'rm'
if scenario == 'error': items[0]['preview_error'] = 'fixture incomplete'
if scenario == 'missing': items.pop()
if scenario == 'unknown': items[0]['status'] = 'deleted'
if scenario == 'empty': items[0]['paths'] = []
if scenario == 'duplicate': items[1] = items[0]
if scenario == 'mode': mode = 'trash' if mode == 'rm' else 'rm'
if scenario == 'warning':
    sys.stderr.write('fixture sizing warning\n' * 10000)
    sys.stderr.flush()
if scenario == 'failed': raise SystemExit('fixture failed preview')
print(json.dumps({'dry_run': True, 'delete_mode': mode, 'items': items}))
'''
PROBE = r'''
import Foundation
import AppKit

@main
struct PreviewProbe {
    @MainActor
    static func main() async throws {
        let root = URL(fileURLWithPath: ProcessInfo.processInfo.environment["MACCLEANER_REQUEST_FIXTURES"]!)
        let calls = root.appendingPathComponent("calls")
        let scenario = root.appendingPathComponent("scenario")
        func setScenario(_ value: String) throws { try value.write(to: scenario, atomically: true, encoding: .utf8) }
        _ = NSApplication.shared
        let bridge = CleanerBridge()
        await bridge.ensureSettingsLoaded().value
        bridge.deleteMode = "rm"
        do { _ = try await bridge.previewCleanup(ids: []); fatalError("empty selection accepted") }
        catch { }
        guard !FileManager.default.fileExists(atPath: calls.path) else { fatalError("empty selection reached engine") }
        try setScenario("good")
        let preview = try await bridge.previewCleanup(ids: ["path", "command"])
        guard preview.problem == nil, preview.preview.items[0].paths[0].path == "/fixture/cache",
              preview.preview.items[1].cmd == "fixture cleanup" else { fatalError("scope lost") }
        guard preview.preview.items[0].actionDescription(mode: "trash") == "Move to Trash",
              preview.preview.items[0].actionDescription(mode: "rm") == "Permanently delete",
              preview.preview.items[1].actionDescription(mode: "trash").contains("not recoverable") else {
            fatalError("cleanup consequences mislabeled")
        }
        let trashItem = try JSONDecoder().decode(CleanupPreview.Item.self,
            from: Data(#"{"id":"trash","label":"Trash","status":"would-delete","paths":[]}"#.utf8))
        guard trashItem.actionDescription(mode: "trash") == "Permanently delete" else {
            fatalError("emptying Trash labeled recoverable")
        }
        let before = try String(contentsOf: calls, encoding: .utf8)
        bridge.deleteMode = "trash"
        do { try await bridge.clean(reviewed: preview); fatalError("mode change accepted") }
        catch { }
        guard try String(contentsOf: calls, encoding: .utf8) == before else { fatalError("changed mode launched cleanup") }
        print("ok   frozen IDs, paths and commands; changed mode refuses execution")
        let trash = try await bridge.previewCleanup(ids: ["path", "command"])
        guard trash.problem == nil, trash.mode == "trash" else { fatalError("trash preview failed") }
        for name in ["error", "missing", "unknown", "empty", "duplicate", "mode", "warning"] {
            try setScenario(name)
            let invalid = try await bridge.previewCleanup(ids: ["path", "command"])
            guard invalid.problem != nil else { fatalError("invalid preview accepted: \(name)") }
            let before = try String(contentsOf: calls, encoding: .utf8)
            do { try await bridge.clean(reviewed: invalid); fatalError("invalid preview executed: \(name)") }
            catch { }
            guard try String(contentsOf: calls, encoding: .utf8) == before else { fatalError("invalid preview reached cleanup") }
            print("ok   blocks \(name) preview before cleanup")
        }
        try setScenario("failed")
        do { _ = try await bridge.previewCleanup(ids: ["path", "command"]); fatalError("failed engine accepted") }
        catch { }
        let invocations = try String(contentsOf: calls, encoding: .utf8).split(separator: "\n")
        guard invocations.allSatisfy({ $0.contains("--dry-run") }) else { fatalError("preview mutated data") }
        print("ok   every engine invocation was a dry run; failures propagate")
    }
}
'''

def main():
    with tempfile.TemporaryDirectory(prefix="mc-cleanup-preview-") as td:
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
                raise SystemExit(err or "Cleanup preview probe failed")
        finally:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.communicate(timeout=3)


if __name__ == "__main__":
    main()

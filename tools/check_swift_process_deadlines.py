#!/usr/bin/env python3
"""An external deadline keeps a regressed Swift process probe from hanging CI."""
import os
from pathlib import Path
import signal
import subprocess
import tempfile
import time

REPO = Path(__file__).resolve().parent.parent



def check_shell_completion(root):
    source = root / "main.swift"
    source.write_text(r'''import Foundation
let record = CommandLine.arguments[1]
let status = CommandLine.arguments[2]
let script = "/bin/sleep 10 & child=$!; printf '%s %s' \"$$\" \"$child\" > \"$1\"; printf 7; exit " + status
let output = ReadOnlyProcess.run(["-c", script, "fixture", record],
                                 executable: "/bin/sh", timeout: 1, allowFailure: true)
print(output ?? "nil")
''')
    executable = root / "group-probe"
    runner = REPO / "swift/MacCleanerKit/Sources/MacCleanerKit/ReadOnlyProcess.swift"
    subprocess.run(["swiftc", str(runner), str(source), "-o", str(executable)],
                   check=True, timeout=120)
    for status in (0, 7):
        record = root / f"group-{status}"
        process = subprocess.Popen([str(executable), str(record), str(status)],
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                   text=True, start_new_session=True)
        try:
            out, err = process.communicate(timeout=3)
            if process.returncode or out.strip() != "7":
                raise SystemExit(f"FAIL: Swift shell-exit probe: {out}{err}")
            group, child = map(int, record.read_text().split())
            deadline = time.monotonic() + 1
            while True:
                state = subprocess.run(["/bin/ps", "-p", str(child), "-o", "stat="],
                                       capture_output=True, text=True, timeout=2)
                if state.stderr:
                    raise SystemExit(f"FAIL: cannot inspect fixture child: {state.stderr}")
                if not state.stdout.strip() or state.stdout.lstrip().startswith("Z"):
                    break
                if time.monotonic() >= deadline:
                    raise SystemExit(f"FAIL: Swift descendant survived shell exit {status}")
                time.sleep(0.02)
            print(f"Swift shell exit {status} descendant cleanup passed")
        finally:
            groups = {process.pid}
            if record.exists():
                groups.add(int(record.read_text().split()[0]))
            for group in groups:
                try:
                    os.killpg(group, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            process.communicate(timeout=3)


def main():
    with tempfile.TemporaryDirectory(prefix="mc-process-deadline-") as td:
        root = Path(td)
        source = root / "main.swift"
        source.write_text('''import Foundation
let commands = TmpScanner.runningCommandLines()
guard commands == nil else { fatalError("timed-out ps must be unknown, not idle") }
print("Swift process-list deadline passed")
''')
        executable = root / "probe"
        sources = sorted((REPO / "swift/MacCleanerKit/Sources/MacCleanerKit").glob("*.swift"))
        subprocess.run(["swiftc", *map(str, sources), str(source), "-o", str(executable)],
                       check=True, timeout=120)
        stub = root / "ps"
        stub.write_text("#!/bin/sh\nexec /bin/sleep 30\n")
        stub.chmod(0o755)
        process = subprocess.Popen([str(executable)], env={**os.environ, "PATH": str(root)},
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                   text=True, start_new_session=True)
        try:
            out, err = process.communicate(timeout=12)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            process.communicate()
            raise SystemExit("FAIL: Swift ps probe exceeded its 10-second budget (12-second backstop)")
        if process.returncode:
            raise SystemExit(err or out or "Swift deadline probe failed")
        print(out.strip())
        check_shell_completion(root)


if __name__ == "__main__":
    main()

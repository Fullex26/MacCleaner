#!/usr/bin/env python3
"""An external deadline keeps a regressed Swift process probe from hanging CI."""
import os
from pathlib import Path
import signal
import subprocess
import tempfile

REPO = Path(__file__).resolve().parent.parent


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


if __name__ == "__main__":
    main()

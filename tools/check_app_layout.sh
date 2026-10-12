#!/usr/bin/env bash
# Build and run app/Probes/LayoutProbe.swift against the real app views (macOS
# only). Fails if any sidebar section can demand more height than the window's
# minimum — the Storage-tab "header missing / list under the titlebar" class of
# bug. See the probe's own header comment for the full story.
set -euo pipefail
REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OUT="$(mktemp -d)"
trap 'rm -rf "$OUT"' EXIT
SOURCES=()
for f in "$REPO_DIR"/app/Sources/*.swift; do
    [ "$(basename "$f")" = "MacCleanerApp.swift" ] && continue
    SOURCES+=("$f")
done
swiftc -swift-version 5 -DSPARKLE_DISABLED \
    -target "$(uname -m)-apple-macos13.0" \
    -o "$OUT/layout-probe" \
    "${SOURCES[@]}" "$REPO_DIR/app/Probes/LayoutProbe.swift"
MACCLEANER_ENGINE="$REPO_DIR/cleaner.py" "$OUT/layout-probe"

#!/bin/bash
# Build MacCleaner.app from source with swiftc — no Xcode project needed.
#
# Usage:
#   bash app/build.sh            # → build/MacCleaner.app (universal if possible)
#   bash app/build.sh --install  # …then copy to ~/Applications/MacCleaner.app
set -euo pipefail

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(dirname "$APP_DIR")"
BUILD_DIR="$REPO_DIR/build"
BUNDLE="$BUILD_DIR/MacCleaner.app"
MIN_MACOS="13.0"

echo "🔨 Building MacCleaner.app"

rm -rf "$BUNDLE"
mkdir -p "$BUILD_DIR" "$BUNDLE/Contents/MacOS" "$BUNDLE/Contents/Resources"

SOURCES=("$APP_DIR"/Sources/*.swift)

# Native slice
NATIVE_ARCH="$(uname -m)"
echo "→ Compiling $NATIVE_ARCH slice…"
swiftc -O -swift-version 5 \
    -target "$NATIVE_ARCH-apple-macos$MIN_MACOS" \
    -o "$BUILD_DIR/MacCleaner-$NATIVE_ARCH" \
    "${SOURCES[@]}"

# Cross-compile the other slice for a universal binary; skip if the SDK can't
OTHER_ARCH="x86_64"
[ "$NATIVE_ARCH" = "x86_64" ] && OTHER_ARCH="arm64"
if swiftc -O -swift-version 5 \
    -target "$OTHER_ARCH-apple-macos$MIN_MACOS" \
    -o "$BUILD_DIR/MacCleaner-$OTHER_ARCH" \
    "${SOURCES[@]}" 2>/dev/null; then
    echo "→ Compiled $OTHER_ARCH slice — creating universal binary"
    lipo -create "$BUILD_DIR/MacCleaner-$NATIVE_ARCH" "$BUILD_DIR/MacCleaner-$OTHER_ARCH" \
        -output "$BUNDLE/Contents/MacOS/MacCleaner"
else
    echo "→ $OTHER_ARCH cross-compile unavailable — shipping $NATIVE_ARCH only"
    cp "$BUILD_DIR/MacCleaner-$NATIVE_ARCH" "$BUNDLE/Contents/MacOS/MacCleaner"
fi
rm -f "$BUILD_DIR/MacCleaner-$NATIVE_ARCH" "$BUILD_DIR/MacCleaner-$OTHER_ARCH"

# Bundle metadata + fallback engine (app works even without install.sh)
cp "$APP_DIR/Info.plist" "$BUNDLE/Contents/"
cp "$REPO_DIR/cleaner.py" "$BUNDLE/Contents/Resources/"

# App icon (regenerate via app/icon/generate_icon.py)
if [ -f "$APP_DIR/MacCleaner.icns" ]; then
    echo "→ Copying app icon…"
    cp "$APP_DIR/MacCleaner.icns" "$BUNDLE/Contents/Resources/"
else
    echo "→ Warning: app/MacCleaner.icns not found — building without an app icon"
fi

# Strip extended attributes (Finder/iCloud detritus breaks codesign), then
# ad-hoc sign (required on Apple Silicon; replace with a real identity to distribute)
xattr -cr "$BUNDLE" 2>/dev/null || true
codesign --force --sign - "$BUNDLE" || echo "→ codesign unavailable — unsigned bundle"

echo "✅ Built: $BUNDLE"
lipo -info "$BUNDLE/Contents/MacOS/MacCleaner" 2>/dev/null || true

if [ "${1:-}" = "--install" ]; then
    mkdir -p "$HOME/Applications"
    rm -rf "$HOME/Applications/MacCleaner.app"
    cp -R "$BUNDLE" "$HOME/Applications/MacCleaner.app"
    echo "✅ Installed: ~/Applications/MacCleaner.app"
fi

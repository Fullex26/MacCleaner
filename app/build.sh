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

# --- Sparkle framework: cache-first fetch, checksum-verified -------------
# build/sparkle-<version>/ is gitignored and reused across builds. On a cache
# miss we download the official release archive, verify it against a pinned
# SHA-256 (rejects a corrupted or tampered download), and extract it. Any
# fetch/verify failure is fatal under CI (a release must never silently ship
# without the updater); local/dev builds fall back to -DSPARKLE_DISABLED so
# a flaky network or offline machine never blocks a dev build.
#
# NOTE (bash 3.2, macOS's default /bin/bash): under `set -u`, expanding an
# EMPTY array with "${arr[@]}" is an "unbound variable" error on this bash.
# SPARKLE_SWIFT_FLAGS must therefore always hold at least one element —
# never reset to an empty array.
SPARKLE_VERSION="2.9.5"
SPARKLE_CACHE_DIR="$BUILD_DIR/sparkle-$SPARKLE_VERSION"
SPARKLE_ARCHIVE_URL="https://github.com/sparkle-project/Sparkle/releases/download/$SPARKLE_VERSION/Sparkle-$SPARKLE_VERSION.tar.xz"
# SHA-256 of the official Sparkle-2.9.5.tar.xz release asset (computed once
# from a verified download and pinned here).
SPARKLE_ARCHIVE_SHA256="015336b601493e05c237964954bff6191370003d94edefe663724c88840d73cc"

SPARKLE_SWIFT_FLAGS=(-DSPARKLE_DISABLED)
SPARKLE_ENABLED=0
SPARKLE_VERIFIED_MARKER="$SPARKLE_CACHE_DIR/.verified-sha256"

# A cache hit used to skip the pinned-SHA check entirely -- trusting whatever
# is on disk under build/sparkle-<version>/ just because the directory
# exists and looks right. That silently defeats the checksum pin for every
# build after the first: a tampered or corrupted cache dir (or a directory
# an attacker pre-seeded before CI's first run) would be reused forever.
# Persist the verified hash alongside the cache and require it to match on
# every cache hit too; anything else forces a re-fetch instead of trusting
# stale/tampered bytes.
if [ -d "$SPARKLE_CACHE_DIR/Sparkle.framework" ] \
    && [ -f "$SPARKLE_VERIFIED_MARKER" ] \
    && [ "$(cat "$SPARKLE_VERIFIED_MARKER" 2>/dev/null)" = "$SPARKLE_ARCHIVE_SHA256" ]; then
    echo "→ Sparkle $SPARKLE_VERSION framework cached (checksum verified) — skipping fetch"
    SPARKLE_ENABLED=1
else
    if [ -d "$SPARKLE_CACHE_DIR" ]; then
        echo "→ Cached Sparkle $SPARKLE_VERSION found but not checksum-verified (missing/mismatched marker) — re-fetching"
        rm -rf "$SPARKLE_CACHE_DIR"
    fi
    echo "→ Fetching Sparkle ${SPARKLE_VERSION}…"
    SPARKLE_ARCHIVE="$BUILD_DIR/Sparkle-$SPARKLE_VERSION.tar.xz"
    SPARKLE_FETCH_OK=1

    if ! curl -fsSL "$SPARKLE_ARCHIVE_URL" -o "$SPARKLE_ARCHIVE"; then
        echo "→ Sparkle download failed" >&2
        SPARKLE_FETCH_OK=0
    fi

    if [ "$SPARKLE_FETCH_OK" = "1" ]; then
        SPARKLE_ACTUAL_SHA256="$(shasum -a 256 "$SPARKLE_ARCHIVE" | awk '{print $1}')"
        if [ "$SPARKLE_ACTUAL_SHA256" != "$SPARKLE_ARCHIVE_SHA256" ]; then
            echo "→ Sparkle archive checksum mismatch (expected $SPARKLE_ARCHIVE_SHA256, got $SPARKLE_ACTUAL_SHA256)" >&2
            SPARKLE_FETCH_OK=0
        fi
    fi

    if [ "$SPARKLE_FETCH_OK" = "1" ]; then
        mkdir -p "$SPARKLE_CACHE_DIR"
        if ! tar -xJf "$SPARKLE_ARCHIVE" -C "$SPARKLE_CACHE_DIR"; then
            echo "→ Sparkle archive extraction failed" >&2
            SPARKLE_FETCH_OK=0
        fi
    fi

    rm -f "$SPARKLE_ARCHIVE"

    if [ "$SPARKLE_FETCH_OK" = "1" ] && [ -d "$SPARKLE_CACHE_DIR/Sparkle.framework" ]; then
        echo "→ Sparkle $SPARKLE_VERSION fetched and verified"
        echo "$SPARKLE_ARCHIVE_SHA256" > "$SPARKLE_VERIFIED_MARKER"
        SPARKLE_ENABLED=1
    else
        rm -rf "$SPARKLE_CACHE_DIR"
        if [ -n "${CI:-}" ]; then
            echo "✗ Sparkle fetch/verify failed and CI is set — failing build" >&2
            exit 1
        fi
        echo "⚠️  Continuing without Sparkle (-DSPARKLE_DISABLED) — auto-updates disabled in this build" >&2
    fi
fi

if [ "$SPARKLE_ENABLED" = "1" ]; then
    SPARKLE_SWIFT_FLAGS=(-F "$SPARKLE_CACHE_DIR" -framework Sparkle -Xlinker -rpath -Xlinker @executable_path/../Frameworks)
fi

SOURCES=("$APP_DIR"/Sources/*.swift)

# Native slice
NATIVE_ARCH="$(uname -m)"
echo "→ Compiling $NATIVE_ARCH slice…"
swiftc -O -swift-version 5 \
    "${SPARKLE_SWIFT_FLAGS[@]}" \
    -target "$NATIVE_ARCH-apple-macos$MIN_MACOS" \
    -o "$BUILD_DIR/MacCleaner-$NATIVE_ARCH" \
    "${SOURCES[@]}"

# Cross-compile the other slice for a universal binary; skip if the SDK can't
OTHER_ARCH="x86_64"
[ "$NATIVE_ARCH" = "x86_64" ] && OTHER_ARCH="arm64"
if swiftc -O -swift-version 5 \
    "${SPARKLE_SWIFT_FLAGS[@]}" \
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

# V3 Stage 3: embed the read-only Swift engine (mck) for the dual-engine
# soak. Best-effort — a machine without the Swift toolchain still gets a
# fully working app, just with the soak silently unavailable (the bridge
# checks for the binary before running it). Universal when possible, to
# match the app binary itself.
if command -v swift >/dev/null 2>&1; then
    echo "→ Building mck (V3 soak engine)…"
    if swift build -c release --package-path "$REPO_DIR/swift/MacCleanerKit" \
        --arch arm64 --arch x86_64 >/dev/null 2>&1; then
        MCK_BIN="$REPO_DIR/swift/MacCleanerKit/.build/apple/Products/Release/mck"
    else
        swift build -c release --package-path "$REPO_DIR/swift/MacCleanerKit" >/dev/null 2>&1 || true
        MCK_BIN="$REPO_DIR/swift/MacCleanerKit/.build/release/mck"
    fi
    if [ -x "$MCK_BIN" ]; then
        cp "$MCK_BIN" "$BUNDLE/Contents/Resources/mck"
        echo "→ mck embedded"
    else
        echo "⚠️  mck build failed — app ships without the V3 soak engine" >&2
    fi
else
    echo "⚠️  swift toolchain not found — app ships without the V3 soak engine" >&2
fi

# App icon (regenerate via app/icon/generate_icon.py)
if [ -f "$APP_DIR/MacCleaner.icns" ]; then
    echo "→ Copying app icon…"
    cp "$APP_DIR/MacCleaner.icns" "$BUNDLE/Contents/Resources/"
else
    echo "→ Warning: app/MacCleaner.icns not found — building without an app icon"
fi

# Embed Sparkle.framework (must happen BEFORE codesign — ad-hoc --deep below
# signs everything under Contents/Frameworks as part of the bundle).
if [ "$SPARKLE_ENABLED" = "1" ]; then
    echo "→ Embedding Sparkle.framework…"
    mkdir -p "$BUNDLE/Contents/Frameworks"
    rm -rf "$BUNDLE/Contents/Frameworks/Sparkle.framework"
    cp -R "$SPARKLE_CACHE_DIR/Sparkle.framework" "$BUNDLE/Contents/Frameworks/Sparkle.framework"
fi

# Strip extended attributes (Finder/iCloud detritus breaks codesign), then
# ad-hoc sign (required on Apple Silicon; replace with a real identity to
# distribute). --deep re-signs embedded frameworks/XPC services too — local/
# dev only; release.yml signs Sparkle.framework explicitly, inside-out, with
# the Developer ID identity before signing the app.
xattr -cr "$BUNDLE" 2>/dev/null || true
codesign --force --deep --sign - "$BUNDLE" || echo "→ codesign unavailable — unsigned bundle"

echo "✅ Built: $BUNDLE"
if [ "$SPARKLE_ENABLED" = "1" ]; then
    echo "→ Sparkle: embedded ($SPARKLE_VERSION)"
else
    echo "→ Sparkle: disabled (-DSPARKLE_DISABLED)"
fi
lipo -info "$BUNDLE/Contents/MacOS/MacCleaner" 2>/dev/null || true

if [ "${1:-}" = "--install" ]; then
    mkdir -p "$HOME/Applications"
    rm -rf "$HOME/Applications/MacCleaner.app"
    cp -R "$BUNDLE" "$HOME/Applications/MacCleaner.app"
    echo "✅ Installed: ~/Applications/MacCleaner.app"
fi

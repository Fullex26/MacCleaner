#!/bin/bash
# MacCleaner Install Script
set -e

echo ""
echo "🧹 MacCleaner Installer"
echo "========================"

# 1. Destination
INSTALL_DIR="$HOME/mac-cleaner"
mkdir -p "$INSTALL_DIR"

# 2. Copy files (never clobber the user's existing config)
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cp "$SCRIPT_DIR/cleaner.py" "$INSTALL_DIR/"
cp "$SCRIPT_DIR/scheduler.sh" "$INSTALL_DIR/"
if [ -d "$SCRIPT_DIR/completions" ]; then
    mkdir -p "$INSTALL_DIR/completions"
    cp "$SCRIPT_DIR/completions/"* "$INSTALL_DIR/completions/" 2>/dev/null || true
fi
if [ ! -f "$INSTALL_DIR/config.json" ] && [ -f "$SCRIPT_DIR/config.json" ]; then
    cp "$SCRIPT_DIR/config.json" "$INSTALL_DIR/"
fi
chmod +x "$INSTALL_DIR/cleaner.py"
chmod +x "$INSTALL_DIR/scheduler.sh"

# 3. Install rich for pretty output (optional — plain output works without it)
echo "→ Installing Python dependencies..."
python3 -m pip install rich --quiet --break-system-packages 2>/dev/null || \
python3 -m pip install rich --quiet 2>/dev/null || \
echo "  (rich not installed — plain output mode)"

# 4. Shell aliases
SHELL_RC="$HOME/.zshrc"
if ! grep -q "mac-cleaner" "$SHELL_RC" 2>/dev/null; then
    {
        echo ""
        echo "# MacCleaner"
        echo "alias maccleaner='python3 ~/mac-cleaner/cleaner.py'"
        echo "alias mclean='python3 ~/mac-cleaner/cleaner.py clean'"
        echo "alias mpreview='python3 ~/mac-cleaner/cleaner.py scan'"
        echo "alias mreport='python3 ~/mac-cleaner/cleaner.py report'"
    } >> "$SHELL_RC"
    echo "→ Added shell aliases: maccleaner, mclean, mpreview, mreport"
fi

# 5. Shell completions (own guard — the alias guard above already matches for
# anyone who has ever run this installer, so reusing it would silently skip
# completions for every existing user)
COMPLETIONS_DIR="$INSTALL_DIR/completions"
if [ -d "$COMPLETIONS_DIR" ]; then
    ZSHRC="$HOME/.zshrc"
    if ! grep -q "mac-cleaner/completions" "$ZSHRC" 2>/dev/null; then
        {
            echo ""
            echo "# MacCleaner completions"
            echo "fpath=(\"\$HOME/mac-cleaner/completions\" \$fpath)"
            echo "autoload -Uz compinit && compinit -u"
        } >> "$ZSHRC"
        echo "→ Added zsh completions (restart your shell to use them)"
    fi

    # bash: macOS ships bash 3.2, and there may be no bash-completion install,
    # so source the file directly from whichever rc file bash actually reads.
    for BASHRC in "$HOME/.bash_profile" "$HOME/.bashrc"; do
        [ -f "$BASHRC" ] || continue
        if ! grep -q "mac-cleaner/completions" "$BASHRC" 2>/dev/null; then
            {
                echo ""
                echo "# MacCleaner completions"
                echo "[ -r \"\$HOME/mac-cleaner/completions/maccleaner.bash\" ] && \\"
                echo "    . \"\$HOME/mac-cleaner/completions/maccleaner.bash\""
            } >> "$BASHRC"
            echo "→ Added bash completions to $(basename "$BASHRC")"
        fi
    done
fi

# 6. Schedule (skipped when not running interactively)
if [ -t 0 ]; then
    echo ""
    echo "📅 Schedule cleanup?"
    echo "  1) Weekly (every Monday 9am) — recommended"
    echo "  2) Monthly (1st of month)"
    echo "  3) Skip for now"
    read -p "Choice [1/2/3]: " choice || choice=3

    case "$choice" in
        1) bash "$INSTALL_DIR/scheduler.sh" weekly ;;
        2) bash "$INSTALL_DIR/scheduler.sh" monthly ;;
        *) echo "  Skipped — run '$INSTALL_DIR/scheduler.sh weekly' anytime" ;;
    esac
else
    echo "→ Non-interactive install — schedule later with '$INSTALL_DIR/scheduler.sh weekly'"
fi

# 7. Menu bar app: build fresh from source whenever possible, so the app
# installed from a `git clone && bash install.sh` can never be older than the
# checkout — falling back to the committed bundle only when swiftc isn't
# available (the committed bundle is rebuilt each release, but a clone
# between releases would otherwise ship whatever was last committed).
APP_BUNDLE="$SCRIPT_DIR/MacCleaner.app"
APP_DEST="$HOME/Applications/MacCleaner.app"
if command -v swiftc >/dev/null 2>&1 && [ -f "$SCRIPT_DIR/app/build.sh" ]; then
    echo "→ Building MacCleaner.app from source..."
    if bash "$SCRIPT_DIR/app/build.sh"; then
        APP_BUNDLE="$SCRIPT_DIR/build/MacCleaner.app"
    elif [ -d "$APP_BUNDLE" ]; then
        echo "→ Build failed — falling back to the committed app bundle"
    fi
elif [ -d "$APP_BUNDLE" ]; then
    echo "→ swiftc not found — using the committed app bundle (may be older than this checkout)"
fi
if [ -d "$APP_BUNDLE" ]; then
    mkdir -p "$HOME/Applications"
    rm -rf "$APP_DEST"
    cp -R "$APP_BUNDLE" "$APP_DEST" 2>/dev/null || \
    cp -R "$APP_BUNDLE" "/Applications/MacCleaner.app" 2>/dev/null || true
    echo "→ Installed MacCleaner.app to ~/Applications/"
else
    echo "→ No Swift toolchain and no committed app bundle found — skipping menu bar app install"
    echo "  Install Xcode Command Line Tools and re-run, or run 'bash app/build.sh --install' later"
fi

echo ""
echo "✅ Installation complete!"
echo ""
echo "Commands:"
echo "  maccleaner            — show help & available commands"
echo "  maccleaner scan       — see what can be cleaned"
echo "  maccleaner clean      — interactive cleanup"
echo "  maccleaner projects   — find stale build artifacts"
echo "  maccleaner doctor     — check environment health"
echo "  maccleaner report     — show history"
echo ""
echo "For AI agents: maccleaner scan --json  (full contract in AGENTS.md)"
echo ""
echo "Menu bar app: open ~/Applications/MacCleaner.app  (look for 🧹)"
echo ""
echo "Restart your terminal or run: source ~/.zshrc"
echo ""

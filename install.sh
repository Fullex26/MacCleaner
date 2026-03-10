#!/bin/bash
# MacCleaner Install Script
set -e

echo ""
echo "🧹 MacCleaner Installer"
echo "========================"

# 1. Destination
INSTALL_DIR="$HOME/mac-cleaner"
mkdir -p "$INSTALL_DIR"

# 2. Copy files
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cp "$SCRIPT_DIR/cleaner.py" "$INSTALL_DIR/"
cp "$SCRIPT_DIR/scheduler.sh" "$INSTALL_DIR/"
cp "$SCRIPT_DIR/config.json" "$INSTALL_DIR/" 2>/dev/null || true
chmod +x "$INSTALL_DIR/cleaner.py"
chmod +x "$INSTALL_DIR/scheduler.sh"

# 3. Install rich for pretty output
echo "→ Installing Python dependencies..."
python3 -m pip install rich --quiet --break-system-packages 2>/dev/null || \
python3 -m pip install rich --quiet 2>/dev/null || \
echo "  (rich not installed — plain output mode)"

# 4. Shell alias
SHELL_RC="$HOME/.zshrc"
if ! grep -q "mac-cleaner" "$SHELL_RC" 2>/dev/null; then
    echo "" >> "$SHELL_RC"
    echo "# MacCleaner" >> "$SHELL_RC"
    echo "alias maccleaner='python3 ~/mac-cleaner/cleaner.py'" >> "$SHELL_RC"
    echo "alias mclean='python3 ~/mac-cleaner/cleaner.py --clean'" >> "$SHELL_RC"
    echo "alias mpreview='python3 ~/mac-cleaner/cleaner.py --preview'" >> "$SHELL_RC"
    echo "alias mreport='python3 ~/mac-cleaner/cleaner.py --report'" >> "$SHELL_RC"
    echo "→ Added shell aliases: maccleaner, mclean, mpreview, mreport"
fi

# 5. Schedule
echo ""
echo "📅 Schedule cleanup?"
echo "  1) Weekly (every Monday 9am) — recommended"
echo "  2) Monthly (1st of month)"
echo "  3) Skip for now"
read -p "Choice [1/2/3]: " choice

case "$choice" in
    1) bash "$INSTALL_DIR/scheduler.sh" weekly ;;
    2) bash "$INSTALL_DIR/scheduler.sh" monthly ;;
    *) echo "  Skipped — run '$INSTALL_DIR/scheduler.sh weekly' anytime" ;;
esac

# 6. Install menu bar app
APP_BUNDLE="$SCRIPT_DIR/MacCleaner.app"
APP_DEST="$HOME/Applications/MacCleaner.app"
if [ -d "$APP_BUNDLE" ]; then
    mkdir -p "$HOME/Applications"
    rm -rf "$APP_DEST"
    cp -r "$APP_BUNDLE" "$APP_DEST" 2>/dev/null || \
    cp -r "$APP_BUNDLE" "/Applications/MacCleaner.app" 2>/dev/null || true
    echo "→ Copied MacCleaner.app to ~/Applications/"
fi

echo ""
echo "🖥️  Menu Bar App"
echo "  To run the menu bar app:"
echo "  1. Open: ~/Applications/MacCleaner.app"
echo "     (or: open ~/Applications/MacCleaner.app)"
echo "  2. Look for 🧹 in your menu bar"
echo ""
echo "✅ Installation complete!"
echo ""
echo "Commands:"
echo "  maccleaner          — show help & available commands"
echo "  maccleaner preview  — see what will be deleted"
echo "  maccleaner clean    — interactive cleanup"
echo "  maccleaner report   — show history"
echo ""
echo "Restart your terminal or run: source ~/.zshrc"
echo ""

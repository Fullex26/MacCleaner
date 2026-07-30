#!/bin/bash
# MacCleaner Scheduler — install or remove the launchd agents
#
# Two agents:
#   com.fullex.maccleaner.clean      weekly/monthly scheduled clean (--notify)
#   com.fullex.maccleaner.diskwatch  hourly low-disk check
#
# launchd rather than cron: launchd runs a missed calendar job after the Mac
# wakes; cron silently skips it.

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CLEANER="$SCRIPT_DIR/cleaner.py"
LOG="$SCRIPT_DIR/cron.log"          # same path v1/v2.1 used; users know it
PYTHON="$(command -v python3)"

# Overridable for tests only; defaults to the real per-user agents directory.
AGENTS_DIR="${MACCLEANER_LAUNCH_AGENTS_DIR:-$HOME/Library/LaunchAgents}"
CLEAN_LABEL="com.fullex.maccleaner.clean"
WATCH_LABEL="com.fullex.maccleaner.diskwatch"

write_plist() {
    # $1 = label, $2 = XML for program args, $3 = XML for the trigger
    local label="$1" args_xml="$2" trigger_xml="$3"
    mkdir -p "$AGENTS_DIR"
    cat > "$AGENTS_DIR/$label.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>$label</string>
    <key>ProgramArguments</key>
    <array>
$args_xml
    </array>
$trigger_xml
    <key>StandardOutPath</key>
    <string>$LOG</string>
    <key>StandardErrorPath</key>
    <string>$LOG</string>
</dict>
</plist>
PLIST
}

bootstrap() {
    local label="$1" plist="$AGENTS_DIR/$1.plist"
    launchctl bootout "gui/$UID/$label" 2>/dev/null
    if ! launchctl bootstrap "gui/$UID" "$plist" 2>/dev/null; then
        # Older macOS, or a launchd that dislikes bootstrap for this domain
        launchctl unload "$plist" 2>/dev/null
        if ! launchctl load "$plist" 2>/dev/null; then
            echo "⚠️  Could not load $label with launchctl." >&2
            echo "    The plist is written to $plist — load it manually with:" >&2
            echo "    launchctl bootstrap gui/$UID \"$plist\"" >&2
            return 1
        fi
    fi
}

unload_agent() {
    local label="$1"
    launchctl bootout "gui/$UID/$label" 2>/dev/null || launchctl unload "$AGENTS_DIR/$label.plist" 2>/dev/null
    rm -f "$AGENTS_DIR/$label.plist"
}

install_diskwatch() {
    write_plist "$WATCH_LABEL" \
"        <string>$PYTHON</string>
        <string>$CLEANER</string>
        <string>disk-check</string>" \
"    <key>StartInterval</key>
    <integer>3600</integer>"
    bootstrap "$WATCH_LABEL"
}

install_clean() {
    # $1 = "weekly" | "monthly"
    local trigger
    if [ "$1" = "monthly" ]; then
        trigger="    <key>StartCalendarInterval</key>
    <dict>
        <key>Day</key>
        <integer>1</integer>
        <key>Hour</key>
        <integer>9</integer>
        <key>Minute</key>
        <integer>0</integer>
    </dict>"
    else
        trigger="    <key>StartCalendarInterval</key>
    <dict>
        <key>Weekday</key>
        <integer>1</integer>
        <key>Hour</key>
        <integer>9</integer>
        <key>Minute</key>
        <integer>0</integer>
    </dict>"
    fi
    write_plist "$CLEAN_LABEL" \
"        <string>$PYTHON</string>
        <string>$CLEANER</string>
        <string>clean</string>
        <string>--yes</string>
        <string>--notify</string>" \
"$trigger"
    bootstrap "$CLEAN_LABEL"
}

install_schedule() {
    install_clean "$1"
    install_diskwatch
    if [ "$1" = "monthly" ]; then
        echo "✅ Scheduled: 1st of every month at 9am (launchd)"
    else
        echo "✅ Scheduled: every Monday at 9am (launchd)"
    fi
    echo "   Low-disk check: hourly"
    echo "   Log: $LOG"
}

# Migrate a legacy cron line, if any, to launchd. Idempotent.
migrate_cron() {
    local existing
    existing="$(crontab -l 2>/dev/null)" || return 0
    case "$existing" in
        *cleaner.py*) ;;
        *) return 0 ;;
    esac
    local kind="weekly"
    # A monthly cron line pins day-of-month (field 3); weekly pins weekday (field 5).
    if echo "$existing" | grep "cleaner.py" | awk '{print $3}' | grep -qv '^\*$'; then
        kind="monthly"
    fi
    echo "→ Migrating your cron schedule to launchd ($kind)…"
    echo "$existing" | grep -v "cleaner.py" | crontab -
    install_schedule "$kind"
    echo "   Removed the old cron entry."
}

status() {
    echo "── MacCleaner Scheduler Status ──"
    local found=0
    for label in "$CLEAN_LABEL" "$WATCH_LABEL"; do
        if [ -f "$AGENTS_DIR/$label.plist" ]; then
            echo "✅ $label (launchd)"
            found=1
        fi
    done
    [ "$found" = 0 ] && echo "❌ Not scheduled (run ./scheduler.sh weekly)"
    if crontab -l 2>/dev/null | grep -q "cleaner.py"; then
        echo "⚠️  A legacy cron entry is still present — run ./scheduler.sh weekly to migrate."
    fi
}

migrate_cron

case "${1:-}" in
    weekly)   install_schedule weekly ;;
    monthly)  install_schedule monthly ;;
    remove)
        unload_agent "$CLEAN_LABEL"
        unload_agent "$WATCH_LABEL"
        echo "✅ Removed MacCleaner launchd agents"
        ;;
    status)   status ;;
    *)
        echo "MacCleaner Scheduler (launchd)"
        echo ""
        echo "Usage: ./scheduler.sh [command]"
        echo ""
        echo "  weekly   — Clean every Monday at 9am + hourly low-disk check"
        echo "  monthly  — Clean on the 1st of each month + hourly low-disk check"
        echo "  remove   — Remove the scheduled agents"
        echo "  status   — Show current schedule"
        echo ""
        echo "An existing cron schedule is migrated to launchd automatically."
        ;;
esac

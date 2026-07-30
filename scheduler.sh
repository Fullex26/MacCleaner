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
    local err
    launchctl bootout "gui/$UID/$label" 2>/dev/null
    if err="$(launchctl bootstrap "gui/$UID" "$plist" 2>&1 >/dev/null)"; then
        return 0
    fi
    # Older macOS, or a launchd that dislikes bootstrap for this domain
    launchctl unload "$plist" 2>/dev/null
    if err="$(launchctl load "$plist" 2>&1 >/dev/null)"; then
        return 0
    fi
    echo "⚠️  Could not load $label with launchctl.${err:+ ($err)}" >&2
    echo "    The plist is written to $plist — load it manually with:" >&2
    echo "    launchctl bootstrap gui/$UID \"$plist\"" >&2
    return 1
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
    local kind="$1" failed=0
    if ! install_clean "$kind"; then
        echo "⚠️  $CLEAN_LABEL did not load — fix the issue above, then run ./scheduler.sh $kind again (or load the plist manually)." >&2
        failed=1
    fi
    if ! install_diskwatch; then
        echo "⚠️  $WATCH_LABEL did not load — fix the issue above, then run ./scheduler.sh $kind again (or load the plist manually)." >&2
        failed=1
    fi
    if [ "$failed" = 1 ]; then
        echo "❌ Scheduling incomplete — see the warning(s) above." >&2
        return 1
    fi
    if [ "$kind" = "monthly" ]; then
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

# Auto-migration only runs for the install commands (weekly/monthly) below —
# `status`, `remove`, and a bare invocation must never mutate the crontab or
# the agents directory.

case "${1:-}" in
    weekly)   migrate_cron; install_schedule weekly ;;
    monthly)  migrate_cron; install_schedule monthly ;;
    remove)
        existing="$(crontab -l 2>/dev/null)" || existing=""
        case "$existing" in
            *cleaner.py*)
                echo "$existing" | grep -v "cleaner.py" | crontab -
                echo "   Also removed a legacy cron entry."
                ;;
        esac
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

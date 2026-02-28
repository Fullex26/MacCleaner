#!/bin/bash
# MacCleaner Scheduler — install or remove the weekly cron job

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CLEANER="$SCRIPT_DIR/cleaner.py"
LOG="$SCRIPT_DIR/report.log"
PYTHON=$(which python3)

CRON_WEEKLY="0 9 * * 1 $PYTHON $CLEANER --clean --yes >> $SCRIPT_DIR/cron.log 2>&1"
CRON_MONTHLY="0 9 1 * * $PYTHON $CLEANER --clean --yes >> $SCRIPT_DIR/cron.log 2>&1"

install_weekly() {
    (crontab -l 2>/dev/null | grep -v "cleaner.py"; echo "$CRON_WEEKLY") | crontab -
    echo "✅ Scheduled: every Monday at 9am"
    echo "   Log: $SCRIPT_DIR/cron.log"
}

install_monthly() {
    (crontab -l 2>/dev/null | grep -v "cleaner.py"; echo "$CRON_MONTHLY") | crontab -
    echo "✅ Scheduled: 1st of every month at 9am"
    echo "   Log: $SCRIPT_DIR/cron.log"
}

uninstall() {
    crontab -l 2>/dev/null | grep -v "cleaner.py" | crontab -
    echo "✅ Removed MacCleaner from cron"
}

status() {
    echo "── MacCleaner Scheduler Status ──"
    if crontab -l 2>/dev/null | grep -q "cleaner.py"; then
        echo "✅ Active:"
        crontab -l | grep "cleaner.py"
    else
        echo "❌ Not scheduled"
    fi
}

case "$1" in
    weekly)   install_weekly ;;
    monthly)  install_monthly ;;
    remove)   uninstall ;;
    status)   status ;;
    *)
        echo "MacCleaner Scheduler"
        echo ""
        echo "Usage: ./scheduler.sh [command]"
        echo ""
        echo "  weekly   — Run cleanup every Monday at 9am"
        echo "  monthly  — Run cleanup 1st of every month"
        echo "  remove   — Remove scheduled job"
        echo "  status   — Show current schedule"
        ;;
esac

#!/bin/bash
# MacCleaner Scheduler — thin wrapper over `cleaner.py schedule ...`.
# The scheduling logic lives in the engine (single source of truth, and the
# app's Settings drives the same code). This wrapper keeps every documented
# invocation working: weekly | monthly | remove | status.

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CLEANER="$SCRIPT_DIR/cleaner.py"
PYTHON="$(command -v python3)"

if [ -z "$PYTHON" ]; then
    echo "⚠️  python3 not found on PATH — cannot manage the schedule." >&2
    exit 1
fi

case "${1:-}" in
    weekly|monthly) exec "$PYTHON" "$CLEANER" schedule "$1" ;;
    remove)         exec "$PYTHON" "$CLEANER" schedule off ;;
    status)         exec "$PYTHON" "$CLEANER" schedule status ;;
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
        echo "An existing cron schedule is migrated to launchd automatically"
        echo "when you pick weekly or monthly. (Same as: maccleaner schedule ...)"
        ;;
esac

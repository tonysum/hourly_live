#!/bin/bash
# Hourly Paper Trading — startup script
# Usage: ./run.sh [start|status|trades|signals] [options]

set -e
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

# Activate venv — look in package dir first, then parent (duo/.venv)
if [ -d "$SCRIPT_DIR/.venv" ]; then
    source "$SCRIPT_DIR/.venv/bin/activate"
elif [ -d "$SCRIPT_DIR/venv" ]; then
    source "$SCRIPT_DIR/venv/bin/activate"
elif [ -d "$SCRIPT_DIR/../../.venv" ]; then
    source "$SCRIPT_DIR/../../.venv/bin/activate"
fi

# When running standalone (not inside duo project), use hourly_live directly
# When inside duo project structure, use backend.hourly_live
CMD="${1:-start}"
shift 2>/dev/null || true

if python -c "import hourly_live" 2>/dev/null; then
    python -m hourly_live "$CMD" "$@"
else
    cd "$SCRIPT_DIR/../.."
    python -m backend.hourly_live "$CMD" "$@"
fi

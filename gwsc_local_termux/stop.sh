#!/data/user/0/com.termux/files/usr/bin/bash
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PID_FILE="$SCRIPT_DIR/server.pid"

echo "[*] Stopping GWSC Local Server..."

# 1. Kill recorded PID if running
if [ -f "$PID_FILE" ]; then
    PID=$(cat "$PID_FILE")
    if kill -0 "$PID" 2>/dev/null; then
        kill "$PID" 2>/dev/null || true
    fi
    rm -f "$PID_FILE"
fi

# 2. Kill any lingering gwsc server processes
PIDS=$(pgrep -f "gwsc_local_termux.server" || true)
if [ -n "$PIDS" ]; then
    echo "[*] Terminating process(es): $PIDS"
    kill $PIDS 2>/dev/null || true
    sleep 0.5
    # Force kill if still lingering
    kill -9 $PIDS 2>/dev/null || true
fi

echo "[+] GWSC Local Server stopped."

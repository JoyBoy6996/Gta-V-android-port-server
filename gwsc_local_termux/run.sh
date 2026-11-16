#!/data/user/0/com.termux/files/usr/bin/bash
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR/.."
export PYTHONPATH="$SCRIPT_DIR/..:$PYTHONPATH"

PID_FILE="$SCRIPT_DIR/server.pid"
LOG_FILE="$SCRIPT_DIR/logs/gwsc.log"
PYTHON="$SCRIPT_DIR/.venv/bin/python"

if [ ! -f "$PYTHON" ]; then
    PYTHON="$(command -v python3)"
fi

# Stop any previous instance
"$SCRIPT_DIR/stop.sh" >/dev/null 2>&1 || true

mkdir -p "$SCRIPT_DIR/logs"

echo "[*] Starting GWSC Local Server on Port 8443 (UDP Relay 6672)..."
setsid "$PYTHON" -m gwsc_local_termux.server </dev/null >> "$LOG_FILE" 2>&1 &
SERVER_PID=$!
echo $SERVER_PID > "$PID_FILE"

# Wait a moment and check health
sleep 2
if kill -0 "$SERVER_PID" 2>/dev/null; then
    echo "[+] GWSC Local Server started successfully! (PID: $SERVER_PID)"
    echo "    HTTPS Web & API: https://127.0.0.1:8443/"
    echo "    Server Info:     https://127.0.0.1:8443/gwsc/api/info"
    echo "    UDP Relay Port:  6672"
    echo "    Log file:        $LOG_FILE"
else
    echo "[-] Failed to start server. Recent log lines ($LOG_FILE):"
    tail -n 15 "$LOG_FILE"
    exit 1
fi

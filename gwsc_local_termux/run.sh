#!/data/user/0/com.termux/files/usr/bin/bash
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR/.."
export PYTHONPATH="$SCRIPT_DIR/..:$PYTHONPATH"

PID_FILE="$SCRIPT_DIR/server.pid"
LOG_FILE="$SCRIPT_DIR/logs/gwsc.log"
PYTHON="$SCRIPT_DIR/.venv/bin/python"

if [ ! -x "$PYTHON" ]; then
    PYTHON="$(command -v python3)"
fi

"$SCRIPT_DIR/stop.sh" >/dev/null 2>&1 || true
mkdir -p "$SCRIPT_DIR/logs"

echo "[*] Starting GWSC server on port ${GWSC_PORT:-8443} (UDP relay ${GWSC_RELAY_PORT:-6672})..."
if command -v setsid >/dev/null 2>&1; then
    setsid "$PYTHON" -m gwsc_local_termux.server </dev/null >> "$LOG_FILE" 2>&1 &
else
    nohup "$PYTHON" -m gwsc_local_termux.server </dev/null >> "$LOG_FILE" 2>&1 &
fi
SERVER_PID=$!
echo "$SERVER_PID" > "$PID_FILE"

sleep 2
if kill -0 "$SERVER_PID" 2>/dev/null; then
    echo "[+] Server started (PID: $SERVER_PID)"
    echo "    HTTPS/HTTP: https://127.0.0.1:${GWSC_PORT:-8443}/"
    echo "    Server info: https://127.0.0.1:${GWSC_PORT:-8443}/gwsc/api/info"
    echo "    UDP relay:  ${GWSC_RELAY_PORT:-6672}"
    echo "    Log file:   $LOG_FILE"
else
    echo "[-] Failed to start. Recent log lines:"
    tail -n 20 "$LOG_FILE"
    exit 1
fi

#!/data/user/0/com.termux/files/usr/bin/bash
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

echo "[*] Restarting GWSC Local Server..."
./stop.sh || true
sleep 1
./run.sh

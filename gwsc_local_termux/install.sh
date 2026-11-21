#!/data/user/0/com.termux/files/usr/bin/bash
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

echo "=================================================="
echo " GWSC Private Server - Termux Installation        "
echo "=================================================="

if ! command -v python3 >/dev/null 2>&1; then
    echo "[!] python3 is required. Install it with: pkg install python"
    exit 1
fi
if ! command -v openssl >/dev/null 2>&1; then
    echo "[!] openssl is required. Install it with: pkg install openssl"
    exit 1
fi

echo "[*] Python:  $(python3 --version)"
echo "[*] OpenSSL: $(openssl version)"

# Repair a broken .venv symlink from older packages.
if [ -L ".venv" ] && [ ! -e ".venv" ]; then
    echo "[*] Removing broken .venv symlink"
    rm -f .venv
fi

if [ ! -x ".venv/bin/python" ]; then
    echo "[*] Creating Python virtual environment..."
    python3 -m venv .venv
fi

echo "[*] Installing runtime dependencies..."
.venv/bin/pip install --upgrade pip >/dev/null
.venv/bin/pip install -r requirements.txt

if [ "${1:-}" = "--dev" ]; then
    echo "[*] Installing test dependencies..."
    .venv/bin/pip install -r ../requirements-dev.txt
fi

export PYTHONPATH="$(cd "$SCRIPT_DIR/.." && pwd):$PYTHONPATH"

echo "[*] Generating TLS certificates (cert.pem / key.pem)..."
.venv/bin/python -m gwsc_local_termux.generate_certs

echo "[*] Initializing SQLite database..."
.venv/bin/python -c "from gwsc_local_termux.database import init_db; init_db()"

chmod 700 data logs backups 2>/dev/null || true
chmod 600 cert.pem key.pem 2>/dev/null || true

echo "=================================================="
echo " Installation complete.                           "
echo " Start:      ./run.sh                             "
echo " Stop:       ./stop.sh                            "
echo " Restart:    ./restart.sh                         "
echo " Run tests:  .venv/bin/python -m pytest -q        "
echo "=================================================="

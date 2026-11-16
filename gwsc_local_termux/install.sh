#!/data/user/0/com.termux/files/usr/bin/bash
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

echo "=================================================="
echo " GWSC Local Server - Termux Installation Script   "
echo "=================================================="

# Check Python and OpenSSL
if ! command -v python3 >/dev/null 2>&1; then
    echo "[!] Error: python3 is not installed. Please install it with: pkg install python"
    exit 1
fi

if ! command -v openssl >/dev/null 2>&1; then
    echo "[!] Error: openssl is not installed. Please install it with: pkg install openssl"
    exit 1
fi

echo "[*] Python: $(python3 --version)"
echo "[*] OpenSSL: $(openssl version)"

# Setup virtual environment if not present
if [ ! -d ".venv" ]; then
    # Check if existing venv can be used or create one
    if [ -d "$HOME/gtav/gwsc-termux-test/.venv" ]; then
        echo "[*] Linking existing pre-built virtual environment..."
        ln -s "$HOME/gtav/gwsc-termux-test/.venv" .venv
    else
        echo "[*] Creating Python virtual environment..."
        python3 -m venv .venv
        .venv/bin/pip install --upgrade pip
        .venv/bin/pip install -r requirements.txt
    fi
fi

PYTHON="$SCRIPT_DIR/.venv/bin/python"

export PYTHONPATH="$(cd "$SCRIPT_DIR/.." && pwd):$PYTHONPATH"

echo "[*] Generating SSL certificates (cert.pem / key.pem)..."
$PYTHON -m gwsc_local_termux.generate_certs

echo "[*] Initializing SQLite database..."
$PYTHON -c "from gwsc_local_termux.database import init_db; init_db()"

echo "=================================================="
echo " Installation Complete!                          "
echo " You can now start the server with: ./run.sh     "
echo "=================================================="

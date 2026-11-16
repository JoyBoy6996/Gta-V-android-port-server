import subprocess
import socket
import logging
from pathlib import Path
from .config import SSL_CERT, SSL_KEY

log = logging.getLogger("gwsc.certs")

def get_local_ip() -> str:
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"

def generate_ssl_certificates(force: bool = False):
    if not force and SSL_CERT.exists() and SSL_KEY.exists():
        log.info("SSL certificates already exist: %s, %s", SSL_CERT, SSL_KEY)
        return

    local_ip = get_local_ip()
    log.info("Generating SSL certificates for 127.0.0.1, localhost, and %s...", local_ip)

    san = f"subjectAltName=IP:127.0.0.1,IP:{local_ip},DNS:localhost,DNS:roscaelifer.thevakhovske.eu.org"

    cmd = [
        "openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "3650",
        "-keyout", str(SSL_KEY),
        "-out", str(SSL_CERT),
        "-subj", f"/CN={local_ip}/O=GWSC Local/OU=Game Server",
        "-addext", san
    ]

    res = subprocess.run(cmd, capture_output=True, text=True)
    if res.returncode == 0:
        log.info("SSL certificates generated successfully!")
    else:
        # Fallback without addext if OpenSSL version requires config file
        cmd_fallback = [
            "openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "3650",
            "-keyout", str(SSL_KEY),
            "-out", str(SSL_CERT),
            "-subj", f"/CN=127.0.0.1/O=GWSC Local"
        ]
        res_fb = subprocess.run(cmd_fallback, capture_output=True, text=True)
        if res_fb.returncode == 0:
            log.info("SSL certificates generated via fallback!")
        else:
            log.error("Failed to generate SSL certificates: %s", res.stderr or res_fb.stderr)

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    generate_ssl_certificates(force=True)

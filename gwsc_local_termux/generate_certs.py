import logging
import socket
import subprocess

from . import config

log = logging.getLogger("gwsc.certs")


def get_local_ip() -> str:
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.connect(("8.8.8.8", 80))
        ip = sock.getsockname()[0]
        sock.close()
        return ip
    except Exception:
        return "127.0.0.1"


def _secure_permissions() -> None:
    for path in (config.SSL_CERT, config.SSL_KEY):
        try:
            path.chmod(0o600)
        except OSError:
            pass


def generate_ssl_certificates(force: bool = False) -> None:
    if not force and config.SSL_CERT.exists() and config.SSL_KEY.exists():
        _secure_permissions()
        log.info("TLS certificates already exist: %s", config.SSL_CERT)
        return

    local_ip = get_local_ip()
    public_hostname = (config.PUBLIC_HOST or "").split(":")[0].strip()
    san_parts = ["IP:127.0.0.1", f"IP:{local_ip}", "DNS:localhost"]
    if public_hostname:
        san_parts.append(f"DNS:{public_hostname}")
    san = "subjectAltName=" + ",".join(san_parts)

    log.info("Generating self-signed TLS certificate for %s", ", ".join(san_parts))
    cmd = [
        "openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "3650",
        "-keyout", str(config.SSL_KEY),
        "-out", str(config.SSL_CERT),
        "-subj", f"/CN={public_hostname or local_ip}/O=GWSC Private Server/OU=Development",
        "-addext", san,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        log.warning("OpenSSL -addext failed; retrying with a minimal certificate")
        fallback = [
            "openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "3650",
            "-keyout", str(config.SSL_KEY),
            "-out", str(config.SSL_CERT),
            "-subj", f"/CN={public_hostname or local_ip}/O=GWSC Private Server",
        ]
        result = subprocess.run(fallback, capture_output=True, text=True)
    if result.returncode == 0:
        _secure_permissions()
        log.info("TLS certificate generated: %s", config.SSL_CERT)
    else:
        log.error("Could not generate TLS certificates: %s", result.stderr)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    generate_ssl_certificates(force=True)

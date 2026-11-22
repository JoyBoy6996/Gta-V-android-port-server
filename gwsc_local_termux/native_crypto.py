"""Local ECDSA helpers for native P2P certificates and relay discovery.

Los Santos Online proves the following wire contracts:

* ``CreateP2PCertificate`` signs the client's uncompressed P-256 public key
  with a static local CA and returns a PEM certificate chain;
* ``GetRelayServers`` publishes an SPKI public key and signs the advertised
  ``host:port`` string with a relay-specific P-256 key.

The Android client's use of these values is still `UNVERIFIED`, so this module
keeps every private key local, never logs key material and uses the system
``openssl`` CLI rather than inventing a crypto implementation.  Only exact,
validated inputs reach OpenSSL.
"""

import base64
import binascii
import logging
import os
import re
import secrets
import shutil
import subprocess
import tempfile
import threading
from pathlib import Path
from typing import Optional, Tuple

from . import config

log = logging.getLogger("gwsc.native_crypto")

# SOURCE_CONFIRMED (LSO createP2PCertificate.js): SPKI prefix for prime256v1
# followed by a BIT STRING header and one uncompressed EC point.
_P256_SPKI_PREFIX = bytes.fromhex(
    "3059301306072a8648ce3d020106082a8648ce3d030107034200"
)
_P256_UNCOMPRESSED_BYTES = 65
_MAX_B64_INPUT_CHARS = 8192
_HOST_RE = re.compile(r"[A-Za-z0-9._\-:\[\]]{1,253}\Z")
_SAFE_DN_RE = re.compile(r"[^A-Za-z0-9._\- ]")

_p2p_ca_lock = threading.Lock()
_relay_key_lock = threading.Lock()


class NativeCryptoError(Exception):
    """Raised when local key material or a native crypto input is invalid."""


def _openssl(args: list[str], *, input_bytes: Optional[bytes] = None) -> bytes:
    if shutil.which("openssl") is None:
        raise NativeCryptoError("openssl is not available")
    try:
        completed = subprocess.run(
            ["openssl", *args],
            input=input_bytes,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=20,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise NativeCryptoError(f"openssl {args[0]} could not run") from exc
    if completed.returncode != 0:
        # stderr can include file paths and key details; never log or return it.
        raise NativeCryptoError(f"openssl {args[0]} failed")
    return completed.stdout


def _private_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        os.chmod(path, 0o700)
    except OSError:  # pragma: no cover - non-POSIX filesystems
        pass


def _write_file(
    path: Path,
    data: bytes,
    *,
    mode: int,
    private: bool = False,
) -> None:
    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
    if private:
        flags |= getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(path, flags, mode)
    try:
        os.write(fd, data)
    finally:
        os.close(fd)
    os.chmod(path, mode)


def _decode_base64(value: object, field: str) -> bytes:
    if not isinstance(value, str):
        raise NativeCryptoError(f"{field} must be a base64 string")
    raw = value.strip()
    if not raw or len(raw) > _MAX_B64_INPUT_CHARS or re.search(r"\s", raw):
        raise NativeCryptoError(f"{field} is not valid base64")
    try:
        return base64.b64decode(raw, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise NativeCryptoError(f"{field} is not valid base64") from exc


def _safe_dn_component(value: object, *, maximum: int) -> str:
    cleaned = _SAFE_DN_RE.sub("_", str(value)).strip()
    cleaned = cleaned[:maximum] or "unknown"
    return cleaned


def validate_signature_field(value: object) -> None:
    """Require a bounded base64 signature field.

    The meaning of the client's ``sig`` value on Android is `UNVERIFIED`, so it
    is not verified here.  Rejecting malformed or oversized input still keeps
    the endpoint strict without inventing a signature scheme.
    """
    raw = _decode_base64(value, "sig")
    if not 64 <= len(raw) <= 512:
        raise NativeCryptoError("sig has an unexpected length")


def ensure_p2p_ca() -> Tuple[Path, Path]:
    """Return ``(ca_key_path, ca_cert_path)``, generating the static CA once."""
    key_path = config.P2P_CA_KEY_PATH
    cert_path = config.P2P_CA_CERT_PATH
    with _p2p_ca_lock:
        if key_path.exists() != cert_path.exists():
            raise NativeCryptoError(
                "P2P CA is incomplete; restore both files or remove the partial pair"
            )
        if not key_path.exists():
            _private_dir(key_path.parent)
            _cert_dir(cert_path.parent)
            _openssl(
                [
                    "ecparam",
                    "-name",
                    "prime256v1",
                    "-genkey",
                    "-noout",
                    "-out",
                    str(key_path),
                ]
            )
            os.chmod(key_path, 0o600)
            with tempfile.NamedTemporaryFile(
                "w", suffix=".cnf", encoding="utf-8", delete=False
            ) as handle:
                handle.write(
                    "[req]\n"
                    "prompt=no\n"
                    "distinguished_name=dn\n"
                    "x509_extensions=v3_ca\n"
                    "[dn]\n"
                    "C=US\n"
                    "ST=CA\n"
                    "L=San Diego\n"
                    "O=Rockstar Games\n"
                    "OU=Rockstar Games Online\n"
                    "CN=RockstarP2PDTLS\n"
                    "[v3_ca]\n"
                    "basicConstraints=critical,CA:TRUE\n"
                    "keyUsage=critical,digitalSignature,keyCertSign,cRLSign\n"
                    "subjectKeyIdentifier=hash\n"
                    "authorityKeyIdentifier=keyid:always,issuer\n"
                )
                config_path = Path(handle.name)
            try:
                _openssl(
                    [
                        "req",
                        "-new",
                        "-x509",
                        "-key",
                        str(key_path),
                        "-out",
                        str(cert_path),
                        "-config",
                        str(config_path),
                        "-days",
                        "7300",
                        "-sha256",
                    ]
                )
            finally:
                config_path.unlink(missing_ok=True)
            os.chmod(cert_path, 0o644)
            log.info("Generated static local P2P CA")
        else:
            # A corrupted CA must fail closed instead of producing certificates
            # no peer can validate.
            _openssl(["pkey", "-in", str(key_path), "-noout", "-check"])
            _openssl(["x509", "-in", str(cert_path), "-noout"])
    return key_path, cert_path


def _cert_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True, mode=0o755)


def client_public_key_pem(public_key_b64: object) -> bytes:
    """Validate a base64 uncompressed P-256 point and return SPKI PEM."""
    raw = _decode_base64(public_key_b64, "pubKey")
    if len(raw) != _P256_UNCOMPRESSED_BYTES or raw[0] != 0x04:
        raise NativeCryptoError(
            "pubKey must be a 65-byte uncompressed P-256 public key"
        )
    spki_der = _P256_SPKI_PREFIX + raw
    with tempfile.TemporaryDirectory(prefix="gwsc-pubkey-") as directory:
        der_path = Path(directory) / "client.der"
        pem_path = Path(directory) / "client.pem"
        _write_file(der_path, spki_der, mode=0o600, private=True)
        _openssl(
            [
                "pkey",
                "-pubin",
                "-inform",
                "DER",
                "-in",
                str(der_path),
                "-out",
                str(pem_path),
            ]
        )
        return pem_path.read_bytes()


def issue_p2p_certificate(
    *,
    public_key_b64: object,
    rockstar_id: object,
    nickname: object,
    ttl_seconds: int,
) -> Tuple[str, str]:
    """Issue a client certificate for ``public_key_b64`` from the local CA."""
    if not isinstance(ttl_seconds, int) or not 60 <= ttl_seconds <= 30 * 86400:
        raise NativeCryptoError("P2P certificate TTL is out of range")
    public_key_pem = client_public_key_pem(public_key_b64)
    ca_key_path, ca_cert_path = ensure_p2p_ca()
    serial = secrets.token_hex(16)
    sn = _safe_dn_component(rockstar_id, maximum=32)
    cn = _safe_dn_component(nickname, maximum=64)

    with tempfile.TemporaryDirectory(prefix="gwsc-p2p-cert-") as directory:
        root = Path(directory)
        pub_path = root / "client.pub.pem"
        dummy_key_path = root / "dummy.key"
        csr_path = root / "request.csr"
        config_path = root / "openssl.cnf"
        cert_path = root / "client.cert.pem"

        _write_file(pub_path, public_key_pem, mode=0o600, private=True)
        _openssl(
            [
                "ecparam",
                "-name",
                "prime256v1",
                "-genkey",
                "-noout",
                "-out",
                str(dummy_key_path),
            ]
        )
        os.chmod(dummy_key_path, 0o600)
        config_path.write_text(
            "[req]\n"
            "prompt=no\n"
            "distinguished_name=dn\n"
            "[dn]\n"
            f"SN={sn}\n"
            f"CN=prod.gta5.ps4.NP 2 {cn}\n",
            encoding="utf-8",
        )
        os.chmod(config_path, 0o600)
        _openssl(
            [
                "req",
                "-new",
                "-key",
                str(dummy_key_path),
                "-out",
                str(csr_path),
                "-config",
                str(config_path),
                "-nodes",
            ]
        )
        _openssl(
            [
                "x509",
                "-req",
                "-in",
                str(csr_path),
                "-force_pubkey",
                str(pub_path),
                "-CA",
                str(ca_cert_path),
                "-CAkey",
                str(ca_key_path),
                "-out",
                str(cert_path),
                "-days",
                str(ttl_seconds // 86400 or 1),
                "-sha256",
                "-set_serial",
                f"0x{serial}",
            ]
        )
        return cert_path.read_text(encoding="utf-8"), ca_cert_path.read_text(
            encoding="utf-8"
        )


def _der_length(data: bytes, position: int) -> Tuple[int, int]:
    if position >= len(data):
        raise NativeCryptoError("Invalid DER signature")
    first = data[position]
    position += 1
    if first < 0x80:
        return first, position
    count = first & 0x7F
    if count == 0 or count > 4 or position + count > len(data):
        raise NativeCryptoError("Invalid DER signature")
    return int.from_bytes(data[position : position + count], "big"), position + count


def der_signature_to_raw(der_signature: bytes) -> bytes:
    """Convert a P-256 ECDSA DER signature to the 64-byte ``r||s`` wire form."""
    if not der_signature or der_signature[0] != 0x30:
        raise NativeCryptoError("Invalid DER signature")
    length, position = _der_length(der_signature, 1)
    end = position + length
    if end != len(der_signature):
        raise NativeCryptoError("Invalid DER signature")

    components = []
    for _ in range(2):
        if position >= end or der_signature[position] != 0x02:
            raise NativeCryptoError("Invalid DER signature")
        position += 1
        component_length, position = _der_length(der_signature, position)
        if (
            component_length == 0
            or position + component_length > end
        ):
            raise NativeCryptoError("Invalid DER signature")
        component = der_signature[position : position + component_length]
        position += component_length
        if component[0] == 0x00:
            component = component[1:]
        if len(component) > 32:
            raise NativeCryptoError("Invalid DER signature")
        components.append(component.rjust(32, b"\x00"))

    if position != end:
        raise NativeCryptoError("Invalid DER signature")
    return components[0] + components[1]


def raw_signature_to_der(raw_signature: bytes) -> bytes:
    """Convert a 64-byte ``r||s`` signature back to DER (used by tests)."""
    if len(raw_signature) != 64:
        raise NativeCryptoError("Raw signature must be 64 bytes")

    def encode_integer(value: bytes) -> bytes:
        value = value.lstrip(b"\x00") or b"\x00"
        if value[0] & 0x80:
            value = b"\x00" + value
        return bytes([0x02, len(value)]) + value

    body = encode_integer(raw_signature[:32]) + encode_integer(raw_signature[32:])
    return bytes([0x30, len(body)]) + body


def ensure_relay_signing_key() -> Path:
    """Return the relay signing key path, generating the pair when missing."""
    key_path = config.RELAY_SIGNING_KEY_PATH
    public_der_path = config.RELAY_PUBLIC_KEY_DER_PATH
    public_pem_path = config.RELAY_PUBLIC_KEY_PEM_PATH
    public_txt_path = config.RELAY_PUBLIC_KEY_TXT_PATH

    with _relay_key_lock:
        if key_path.exists():
            _openssl(["pkey", "-in", str(key_path), "-noout", "-check"])
        else:
            _private_dir(key_path.parent)
            _openssl(
                [
                    "ecparam",
                    "-name",
                    "prime256v1",
                    "-genkey",
                    "-noout",
                    "-out",
                    str(key_path),
                ]
            )
            os.chmod(key_path, 0o600)
            log.info("Generated static local relay signing key")

        if (
            not public_der_path.exists()
            or not public_pem_path.exists()
            or not public_txt_path.exists()
        ):
            _cert_dir(public_der_path.parent)
            _openssl(
                [
                    "pkey",
                    "-in",
                    str(key_path),
                    "-pubout",
                    "-outform",
                    "DER",
                    "-out",
                    str(public_der_path),
                ]
            )
            _openssl(
                [
                    "pkey",
                    "-in",
                    str(key_path),
                    "-pubout",
                    "-out",
                    str(public_pem_path),
                ]
            )
            public_b64 = base64.b64encode(public_der_path.read_bytes()).decode("ascii")
            _write_file(public_txt_path, public_b64.encode("ascii"), mode=0o644)
    return key_path


def relay_public_key_b64() -> str:
    ensure_relay_signing_key()
    return config.RELAY_PUBLIC_KEY_TXT_PATH.read_text(encoding="ascii").strip()


def validate_relay_host(address: object, port: object) -> str:
    if not isinstance(address, str) or not _HOST_RE.fullmatch(address.strip()):
        raise NativeCryptoError("Relay public address is invalid")
    try:
        parsed_port = int(port)
    except (TypeError, ValueError) as exc:
        raise NativeCryptoError("Relay public port is invalid") from exc
    if not 1 <= parsed_port <= 65535:
        raise NativeCryptoError("Relay public port is invalid")
    return f"{address.strip()}:{parsed_port}"


def sign_relay_host(host: str) -> str:
    if not host or len(host) > 320:
        raise NativeCryptoError("Relay host is invalid")
    key_path = ensure_relay_signing_key()
    der_signature = _openssl(
        ["dgst", "-sha256", "-sign", str(key_path)],
        input_bytes=host.encode("ascii"),
    )
    raw = der_signature_to_raw(der_signature)
    return base64.b64encode(raw).decode("ascii")

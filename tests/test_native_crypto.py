"""Local P2P CA and relay signing helpers.

Key generation is real OpenSSL, but every key in this test lives in a fresh
temporary directory and no private key material is logged or asserted.
"""

import base64
import subprocess
import tempfile
from pathlib import Path

import pytest

from gwsc_local_termux import config, native_crypto


@pytest.fixture()
def isolated_keys(tmp_path, monkeypatch):
    monkeypatch.setattr(
        config, "P2P_CA_KEY_PATH", tmp_path / "p2p" / "ca-key.pem"
    )
    monkeypatch.setattr(
        config, "P2P_CA_CERT_PATH", tmp_path / "p2p" / "ca-cert.pem"
    )
    monkeypatch.setattr(
        config, "RELAY_SIGNING_KEY_PATH", tmp_path / "relay" / "relay.key"
    )
    monkeypatch.setattr(
        config, "RELAY_PUBLIC_KEY_DER_PATH", tmp_path / "relay" / "relay.der"
    )
    monkeypatch.setattr(
        config, "RELAY_PUBLIC_KEY_PEM_PATH", tmp_path / "relay" / "relay.pem"
    )
    monkeypatch.setattr(
        config, "RELAY_PUBLIC_KEY_TXT_PATH", tmp_path / "relay" / "relay.txt"
    )
    return tmp_path


def _openssl(args, *, input_bytes=None):
    return subprocess.run(
        ["openssl", *args],
        input=input_bytes,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=True,
    )


def _client_public_key(directory: Path):
    """Return (base64 uncompressed point, SPKI DER)."""
    key = directory / "client.key"
    der = directory / "client.der"
    _openssl(
        ["ecparam", "-name", "prime256v1", "-genkey", "-noout", "-out", str(key)]
    )
    _openssl(
        ["pkey", "-in", str(key), "-pubout", "-outform", "DER", "-out", str(der)]
    )
    spki = der.read_bytes()
    point = spki[-65:]
    assert point[0] == 0x04
    return base64.b64encode(point).decode("ascii"), spki


@pytest.mark.parametrize(
    "bad", ["", "AAAA", "not base64", "0" * 87 + "="]
)
def test_client_public_key_rejects_invalid_values(bad):
    with pytest.raises(native_crypto.NativeCryptoError):
        native_crypto.client_public_key_pem(bad)


def test_p2p_certificate_is_signed_by_static_ca_and_uses_client_key(isolated_keys):
    public_key_b64, spki = _client_public_key(isolated_keys)
    cert_text, ca_text = native_crypto.issue_p2p_certificate(
        public_key_b64=public_key_b64,
        rockstar_id=10001,
        nickname="tester",
        ttl_seconds=86400,
    )

    assert cert_text.count("BEGIN CERTIFICATE") == 1
    assert ca_text.count("BEGIN CERTIFICATE") == 1
    assert "BEGIN PRIVATE KEY" not in cert_text + ca_text

    cert_path = isolated_keys / "issued.pem"
    ca_path = isolated_keys / "ca.pem"
    cert_path.write_text(cert_text, encoding="utf-8")
    ca_path.write_text(ca_text, encoding="utf-8")
    _openssl(["verify", "-CAfile", str(ca_path), str(cert_path)])

    cert_pub_pem = isolated_keys / "cert.pub.pem"
    cert_pub_der = isolated_keys / "cert.pub.der"
    _openssl(["x509", "-in", str(cert_path), "-pubkey", "-noout", "-out", str(cert_pub_pem)])
    _openssl(
        [
            "pkey",
            "-pubin",
            "-in",
            str(cert_pub_pem),
            "-pubout",
            "-outform",
            "DER",
            "-out",
            str(cert_pub_der),
        ]
    )
    assert cert_pub_der.read_bytes() == spki

    assert oct(config.P2P_CA_KEY_PATH.stat().st_mode & 0o777) == "0o600"


def test_p2p_ca_is_reused_and_partial_ca_fails_closed(isolated_keys):
    public_key_b64, _ = _client_public_key(isolated_keys)
    native_crypto.issue_p2p_certificate(
        public_key_b64=public_key_b64,
        rockstar_id=1,
        nickname="first",
        ttl_seconds=86400,
    )
    first_key = config.P2P_CA_KEY_PATH.read_bytes()
    first_cert = config.P2P_CA_CERT_PATH.read_bytes()

    second_dir = isolated_keys / "second"
    second_dir.mkdir(exist_ok=True)
    second_b64, _ = _client_public_key(second_dir)
    _, second_ca = native_crypto.issue_p2p_certificate(
        public_key_b64=second_b64,
        rockstar_id=2,
        nickname="second",
        ttl_seconds=86400,
    )
    assert first_key == config.P2P_CA_KEY_PATH.read_bytes()
    assert first_cert == config.P2P_CA_CERT_PATH.read_bytes()
    assert second_ca == first_cert.decode("utf-8")

    config.P2P_CA_KEY_PATH.unlink()
    with pytest.raises(native_crypto.NativeCryptoError):
        native_crypto.issue_p2p_certificate(
            public_key_b64=second_b64,
            rockstar_id=2,
            nickname="second",
            ttl_seconds=86400,
        )


def test_der_signature_conversion_round_trip(isolated_keys):
    host = native_crypto.validate_relay_host("203.0.113.10", 6672)
    signature = native_crypto.sign_relay_host(host)
    raw = base64.b64decode(signature)
    assert len(raw) == 64
    assert native_crypto.der_signature_to_raw(native_crypto.raw_signature_to_der(raw)) == raw

    with pytest.raises(native_crypto.NativeCryptoError):
        native_crypto.der_signature_to_raw(b"\x30\x03\x02\x01\x01")


def test_relay_signature_verifies_with_published_public_key(isolated_keys):
    host = native_crypto.validate_relay_host("relay.example.invalid", 61456)
    signature = native_crypto.sign_relay_host(host)
    public_key_b64 = native_crypto.relay_public_key_b64()
    public_der = base64.b64decode(public_key_b64)
    assert len(public_der) == 91

    raw = base64.b64decode(signature)
    signature_der = isolated_keys / "signature.der"
    signature_der.write_bytes(native_crypto.raw_signature_to_der(raw))
    _openssl(
        [
            "dgst",
            "-sha256",
            "-verify",
            str(config.RELAY_PUBLIC_KEY_PEM_PATH),
            "-signature",
            str(signature_der),
        ],
        input_bytes=host.encode("ascii"),
    )
    assert oct(config.RELAY_SIGNING_KEY_PATH.stat().st_mode & 0o777) == "0o600"


@pytest.mark.parametrize(
    "address,port",
    [("", 6672), ("relay.example.invalid/path", 6672), ("relay.example.invalid", 0)],
)
def test_relay_host_validation_rejects_invalid_input(address, port):
    with pytest.raises(native_crypto.NativeCryptoError):
        native_crypto.validate_relay_host(address, port)

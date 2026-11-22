"""Native P2P certificate and relay-discovery endpoints.

The response shapes are ``SOURCE_CONFIRMED`` from Los Santos Online.  Android
compatibility remains `UNVERIFIED`; these tests only assert the private
server's validation, authentication and locally signed material.
"""

import base64
import subprocess
from pathlib import Path
from xml.etree import ElementTree as ET

import pytest

from gwsc_local_termux import config, native_crypto

CREATE_P2P = "/gta5/11/gameservices/auth.asmx/CreateP2PCertificate"
GET_RELAYS = "/gta5/11/gameservices/GeoLocation.asmx/GetRelayServers"
GET_PRESENCE_SERVERS = "/gta5/11/gameservices/Presence.asmx/GetPresenceServers"


@pytest.fixture()
def native_key_paths(tmp_path, monkeypatch):
    root = tmp_path / "native-certs"
    monkeypatch.setattr(config, "P2P_CA_KEY_PATH", root / "p2p" / "ca-key.pem")
    monkeypatch.setattr(config, "P2P_CA_CERT_PATH", root / "p2p" / "ca-cert.pem")
    monkeypatch.setattr(
        config, "RELAY_SIGNING_KEY_PATH", root / "relay" / "relay.key"
    )
    monkeypatch.setattr(
        config, "RELAY_PUBLIC_KEY_DER_PATH", root / "relay" / "relay.der"
    )
    monkeypatch.setattr(
        config, "RELAY_PUBLIC_KEY_PEM_PATH", root / "relay" / "relay.pem"
    )
    monkeypatch.setattr(
        config, "RELAY_PUBLIC_KEY_TXT_PATH", root / "relay" / "relay.txt"
    )
    monkeypatch.setattr(config, "RELAY_PUBLIC_ADDRESS", "203.0.113.10")
    monkeypatch.setattr(config, "RELAY_PUBLIC_PORT", 6672)
    return root


def _root(response):
    return ET.fromstring(response.text)


def _text(root, name):
    node = root.find(f".//{{*}}{name}")
    return node.text if node is not None else None


def _launch_ticket(client, account):
    response = client.post(
        "/gwsc/api/game-launch",
        headers=account["headers"],
        json={"buildReport": "native-p2p-relay"},
    )
    assert response.status_code == 200, response.text
    return response.json()["ticket"]


def _native(client, account):
    # SOURCE_CONFIRMED: LSO's native handlers authenticate with the ticket
    # form field; the Android spelling is UNVERIFIED but harmless to accept.
    return {"ticket": _launch_ticket(client, account), "platformName": "pcros"}


def _public_key_b64(directory: Path) -> str:
    directory.mkdir(parents=True, exist_ok=True)
    key = directory / "client.key"
    der = directory / "client.der"
    subprocess.run(
        [
            "openssl",
            "ecparam",
            "-name",
            "prime256v1",
            "-genkey",
            "-noout",
            "-out",
            str(key),
        ],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        [
            "openssl",
            "pkey",
            "-in",
            str(key),
            "-pubout",
            "-outform",
            "DER",
            "-out",
            str(der),
        ],
        check=True,
        capture_output=True,
    )
    return base64.b64encode(der.read_bytes()[-65:]).decode("ascii")


def test_create_p2p_certificate_returns_signed_chain(
    client, make_account, native_key_paths, tmp_path
):
    account = make_account(email="p2p@example.invalid", nickname="p2pplayer")
    public_key = _public_key_b64(tmp_path / "p2p-client")
    response = client.post(
        CREATE_P2P,
        data={
            **_native(client, account),
            "pubKey": public_key,
            "sig": base64.b64encode(b"\x00" * 64).decode("ascii"),
        },
    )
    assert response.status_code == 200, response.text
    root = _root(response)
    assert _text(root, "Status") == "1"
    chain = _text(root, "CertChain") or ""
    assert chain.count("BEGIN CERTIFICATE") == 2
    assert "BEGIN PRIVATE KEY" not in chain
    assert _text(root, "SecsUntilExpiration") == str(config.P2P_CERT_TTL_SECONDS)


def test_create_p2p_certificate_accepts_case_insensitive_fields(
    client, make_account, native_key_paths, tmp_path
):
    account = make_account(email="p2pcase@example.invalid", nickname="p2pcase")
    public_key = _public_key_b64(tmp_path / "p2p-case-client")
    response = client.post(
        CREATE_P2P,
        data={
            "Ticket": _launch_ticket(client, account),
            "PubKey": public_key,
            "Sig": base64.b64encode(b"\x00" * 64).decode("ascii"),
        },
    )
    assert response.status_code == 200, response.text
    assert _text(_root(response), "Status") == "1"


@pytest.mark.parametrize("public_key", ["AAAA", "not-base64"])
def test_create_p2p_certificate_rejects_invalid_public_key(
    client, make_account, native_key_paths, public_key
):
    account = make_account(email="p2pbad@example.invalid", nickname="p2pbad")
    response = client.post(
        CREATE_P2P,
        data={
            **_native(client, account),
            "pubKey": public_key,
            "sig": base64.b64encode(b"\x00" * 64).decode("ascii"),
        },
    )
    assert response.status_code == 400
    assert _text(_root(response), "Status") == "0"


def test_create_p2p_certificate_requires_pubkey_sig_and_auth(
    client, make_account, native_key_paths, tmp_path
):
    account = make_account(email="p2pmissing@example.invalid", nickname="p2pmiss")
    public_key = _public_key_b64(tmp_path / "p2p-missing-client")

    missing_sig = client.post(
        CREATE_P2P, data={**_native(client, account), "pubKey": public_key}
    )
    assert missing_sig.status_code == 400

    unauthenticated = client.post(
        CREATE_P2P,
        data={
            "pubKey": public_key,
            "sig": base64.b64encode(b"\x00" * 64).decode("ascii"),
        },
    )
    assert unauthenticated.status_code == 401


def test_get_relay_servers_returns_signed_relay(
    client, make_account, native_key_paths
):
    account = make_account(email="relay@example.invalid", nickname="relayuser")
    response = client.post(GET_RELAYS, data=_native(client, account))
    assert response.status_code == 200, response.text
    root = _root(response)
    assert _text(root, "Status") == "1"

    loc_info = root.find(".//{*}LocInfo")
    assert loc_info is not None
    assert loc_info.get("RegionCode") == "5"

    relays = root.find(".//{*}RelaysList")
    assert relays is not None
    assert relays.get("Count") == "1"
    assert relays.get("IsSecure") == "false"
    server = relays.find("{*}Server")
    assert server is not None
    host = native_crypto.validate_relay_host(
        config.RELAY_PUBLIC_ADDRESS, config.RELAY_PUBLIC_PORT
    )
    assert server.get("Host") == host
    assert server.get("IsXblSg") == "false"

    public_key = base64.b64decode(relays.get("PublicKey") or "")
    raw_signature = base64.b64decode(server.get("Sig") or "")
    assert len(public_key) == 91
    assert len(raw_signature) == 64

    signature_der = native_key_paths / "response.sig.der"
    signature_der.parent.mkdir(parents=True, exist_ok=True)
    signature_der.write_bytes(native_crypto.raw_signature_to_der(raw_signature))
    subprocess.run(
        [
            "openssl",
            "dgst",
            "-sha256",
            "-verify",
            str(config.RELAY_PUBLIC_KEY_PEM_PATH),
            "-signature",
            str(signature_der),
        ],
        input=host.encode("ascii"),
        check=True,
        capture_output=True,
    )


def test_get_presence_servers_uses_configured_relay(
    client, make_account, native_key_paths
):
    account = make_account(email="presence@example.invalid", nickname="presence")
    response = client.post(GET_PRESENCE_SERVERS, data=_native(client, account))
    assert response.status_code == 200, response.text
    root = _root(response)
    assert _text(root, "Status") == "1"
    result = root.find(".//{*}Result")
    assert result is not None
    assert result.get("Count") == "1"
    server = result.find("{*}Server")
    assert server is not None
    assert server.get("Host") == "203.0.113.10:6672"
    assert server.get("isSecure") == "false"


def test_get_relay_servers_requires_configuration_and_auth(
    client, make_account, native_key_paths, monkeypatch
):
    account = make_account(email="relaybad@example.invalid", nickname="relaybad")
    unauthenticated = client.post(GET_RELAYS, data={"platformName": "pcros"})
    assert unauthenticated.status_code == 401

    monkeypatch.setattr(config, "RELAY_PUBLIC_ADDRESS", "")
    unconfigured = client.post(GET_RELAYS, data=_native(client, account))
    assert unconfigured.status_code == 503
    assert _text(_root(unconfigured), "Status") == "0"

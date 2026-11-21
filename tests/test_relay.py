import time

from gwsc_local_termux import config
from gwsc_local_termux.multiplayer_relay import MultiplayerUdpRelay, RelayRegistry


class FakeTransport:
    def __init__(self):
        self.sent = []

    def sendto(self, data, addr):
        self.sent.append((data, addr))


def test_registry_routes_only_within_session():
    registry = RelayRegistry()
    registry.register("session-a", 1, ("10.0.0.1", 5001))
    registry.register("session-a", 2, ("10.0.0.2", 5002))
    registry.register("session-b", 3, ("10.0.0.3", 5003))

    protocol = MultiplayerUdpRelay(registry)
    protocol.transport = FakeTransport()
    protocol.datagram_received(b"hello", ("10.0.0.1", 5001))

    assert protocol.transport.sent == [(b"hello", ("10.0.0.2", 5002))]
    assert protocol.stats["forwarded"] == 1


def test_unregistered_and_oversized_packets_are_dropped():
    registry = RelayRegistry()
    protocol = MultiplayerUdpRelay(registry)
    protocol.transport = FakeTransport()

    protocol.datagram_received(b"hello", ("10.0.0.9", 6000))
    assert protocol.stats["dropped_unregistered"] == 1

    registry.register("session-a", 1, ("10.0.0.1", 5001))
    oversized = b"x" * (config.RELAY_MAX_DATAGRAM_BYTES + 1)
    protocol.datagram_received(oversized, ("10.0.0.1", 5001))
    assert protocol.stats["dropped_size"] == 1
    assert protocol.transport.sent == []


def test_registry_expires_stale_peers():
    registry = RelayRegistry()
    peer = registry.register("session-a", 1, ("10.0.0.1", 5001))
    peer.last_seen = time.monotonic() - 3600
    assert registry.expire(timeout_seconds=60) == 1
    assert registry.get(("10.0.0.1", 5001)) is None
    assert registry.stats()["peers"] == 0


def test_relay_registration_requires_session_membership(client, make_account):
    host = make_account(email="host@example.invalid", nickname="host")
    outsider = make_account(email="outsider@example.invalid", nickname="outsider")

    advertised = client.post(
        "/MatchmakingAdvertiseTask",
        data={"scAuthToken": host["token"], "sessionId": "relay-session", "port": "6672"},
    )
    assert advertised.status_code == 200

    registered = client.post(
        "/gwsc/api/relay/register",
        headers=host["headers"],
        json={"sessionId": "relay-session", "udpPort": 7000},
    )
    assert registered.status_code == 200
    assert registered.json()["sessionId"] == "relay-session"

    denied = client.post(
        "/gwsc/api/relay/register",
        headers=outsider["headers"],
        json={"sessionId": "relay-session", "udpPort": 7001},
    )
    assert denied.status_code == 403

    status = client.get("/gwsc/api/relay/status", headers=host["headers"])
    assert status.status_code == 200
    assert len(status.json()["peers"]) == 1

    removed = client.delete("/gwsc/api/relay/register", headers=host["headers"])
    assert removed.status_code == 200

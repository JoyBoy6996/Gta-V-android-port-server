"""Session-aware UDP relay.

The original prototype forwarded every datagram to every peer it had ever
seen.  That leaks traffic between unrelated sessions and lets an unknown
address inject packets.  This implementation only forwards between peers
that registered through an authenticated HTTP call and belong to the same
game session.

The client's exact relay registration handshake has not been captured, so
the HTTP registration endpoint is marked INFERRED in the protocol docs.
"""

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional, Set, Tuple

from . import config

log = logging.getLogger("gwsc.relay")

Address = Tuple[str, int]


@dataclass
class RelayPeer:
    session_id: str
    account_id: int
    address: Address
    last_seen: float = field(default_factory=time.monotonic)
    tokens: float = field(default_factory=lambda: float(config.RELAY_RATE_BURST))
    last_refill: float = field(default_factory=time.monotonic)
    packets: int = 0
    dropped: int = 0

    def allow_packet(self) -> bool:
        now = time.monotonic()
        elapsed = max(0.0, now - self.last_refill)
        self.tokens = min(
            float(config.RELAY_RATE_BURST),
            self.tokens + elapsed * config.RELAY_RATE_PACKETS_PER_SECOND,
        )
        self.last_refill = now
        if self.tokens < 1.0:
            self.dropped += 1
            return False
        self.tokens -= 1.0
        self.packets += 1
        return True


class RelayRegistry:
    def __init__(self) -> None:
        self._peers: Dict[Address, RelayPeer] = {}
        self._sessions: Dict[str, Set[Address]] = {}

    def register(
        self, session_id: str, account_id: int, address: Address
    ) -> RelayPeer:
        if address in self._peers:
            self.unregister(address)
        peers = self._sessions.setdefault(session_id, set())
        if len(peers) >= config.RELAY_MAX_PEERS_PER_SESSION:
            raise ValueError("Session relay is full")
        peer = RelayPeer(session_id=session_id, account_id=account_id, address=address)
        self._peers[address] = peer
        peers.add(address)
        log.info(
            "Relay peer registered: %s:%d account=%d session=%s",
            address[0],
            address[1],
            account_id,
            session_id,
        )
        return peer

    def unregister(self, address: Address) -> Optional[RelayPeer]:
        peer = self._peers.pop(address, None)
        if not peer:
            return None
        peers = self._sessions.get(peer.session_id)
        if peers:
            peers.discard(address)
            if not peers:
                self._sessions.pop(peer.session_id, None)
        return peer

    def unregister_account(self, account_id: int) -> int:
        addresses = [
            address for address, peer in self._peers.items() if peer.account_id == account_id
        ]
        for address in addresses:
            self.unregister(address)
        return len(addresses)

    def unregister_session(self, session_id: str) -> int:
        addresses = list(self._sessions.get(session_id, set()))
        for address in addresses:
            self.unregister(address)
        return len(addresses)

    def get(self, address: Address) -> Optional[RelayPeer]:
        return self._peers.get(address)

    def touch(self, address: Address) -> None:
        peer = self._peers.get(address)
        if peer:
            peer.last_seen = time.monotonic()

    def peers_for(self, session_id: str) -> List[RelayPeer]:
        addresses = self._sessions.get(session_id, set())
        return [self._peers[a] for a in addresses if a in self._peers]

    def peers_for_account(self, account_id: int) -> List[RelayPeer]:
        return [peer for peer in self._peers.values() if peer.account_id == account_id]

    def all_peers(self) -> List[RelayPeer]:
        return list(self._peers.values())

    def expire(self, timeout_seconds: Optional[int] = None) -> int:
        timeout = timeout_seconds or config.RELAY_PEER_TIMEOUT_SECONDS
        cutoff = time.monotonic() - timeout
        stale = [
            address for address, peer in self._peers.items() if peer.last_seen < cutoff
        ]
        for address in stale:
            self.unregister(address)
        if stale:
            log.info("Expired %d stale relay peers", len(stale))
        return len(stale)

    def clear(self) -> None:
        self._peers.clear()
        self._sessions.clear()

    def stats(self) -> Dict[str, object]:
        return {
            "peers": len(self._peers),
            "sessions": len(self._sessions),
            "per_session": {
                session_id: len(addresses)
                for session_id, addresses in self._sessions.items()
            },
        }


relay_registry = RelayRegistry()


class MultiplayerUdpRelay(asyncio.DatagramProtocol):
    def __init__(self, registry: Optional[RelayRegistry] = None):
        self.transport: Optional[asyncio.DatagramTransport] = None
        self.registry = registry or relay_registry
        self.stats = {
            "received": 0,
            "forwarded": 0,
            "dropped_unregistered": 0,
            "dropped_size": 0,
            "dropped_rate": 0,
            "dropped_send": 0,
        }

    def connection_made(self, transport):
        self.transport = transport
        log.info("Multiplayer UDP relay listening")

    def datagram_received(self, data: bytes, addr: Address):
        self.stats["received"] += 1
        if len(data) > config.RELAY_MAX_DATAGRAM_BYTES:
            self.stats["dropped_size"] += 1
            return

        peer = self.registry.get(addr)
        if not peer:
            self.stats["dropped_unregistered"] += 1
            return
        if not peer.allow_packet():
            self.stats["dropped_rate"] += 1
            return

        self.registry.touch(addr)
        for target in self.registry.peers_for(peer.session_id):
            if target.address == addr:
                continue
            try:
                self.transport.sendto(data, target.address)
                self.stats["forwarded"] += 1
            except Exception:
                self.stats["dropped_send"] += 1
                log.warning("Failed to relay packet to %s", target.address, exc_info=True)

    def error_received(self, exc):
        log.error("Relay socket error: %s", exc)

    def connection_lost(self, exc):
        log.info("Relay socket closed")


_relay_transport: Optional[asyncio.DatagramTransport] = None
_relay_protocol: Optional[MultiplayerUdpRelay] = None


async def start_udp_relay(
    host: str, port: int, registry: Optional[RelayRegistry] = None
) -> Optional[asyncio.DatagramTransport]:
    global _relay_transport, _relay_protocol
    loop = asyncio.get_running_loop()

    def factory():
        global _relay_protocol
        _relay_protocol = MultiplayerUdpRelay(registry)
        return _relay_protocol

    try:
        transport, _protocol = await loop.create_datagram_endpoint(
            factory, local_addr=(host, port)
        )
        _relay_transport = transport
        log.info("Started multiplayer UDP relay on %s:%d", host, port)
        return transport
    except Exception:
        log.error("Could not start UDP relay on %s:%d", host, port, exc_info=True)
        return None


def stop_udp_relay() -> None:
    global _relay_transport
    if _relay_transport:
        _relay_transport.close()
        _relay_transport = None


def relay_stats() -> Dict[str, object]:
    stats: Dict[str, object] = {"registry": relay_registry.stats()}
    if _relay_protocol:
        stats["packets"] = dict(_relay_protocol.stats)
    else:
        stats["packets"] = {}
    return stats

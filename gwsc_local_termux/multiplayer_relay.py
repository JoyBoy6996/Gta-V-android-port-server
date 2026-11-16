import asyncio
import logging
from typing import Dict, Set, Tuple

log = logging.getLogger("gwsc.relay")

class MultiplayerUdpRelay(asyncio.DatagramProtocol):
    def __init__(self):
        self.transport = None
        self.clients: Set[Tuple[str, int]] = set()

    def connection_made(self, transport):
        self.transport = transport
        log.info("Multiplayer UDP Relay server listening")

    def datagram_received(self, data: bytes, addr: Tuple[str, int]):
        if addr not in self.clients:
            self.clients.add(addr)
            log.info("New multiplayer peer registered: %s:%d (Total peers: %d)", addr[0], addr[1], len(self.clients))

        # Relay data to all other connected peers
        for client_addr in self.clients:
            if client_addr != addr:
                try:
                    self.transport.sendto(data, client_addr)
                except Exception as e:
                    log.warning("Failed to relay packet to %s: %s", client_addr, e)

    def error_received(self, exc):
        log.error("Relay socket error: %s", exc)

    def connection_lost(self, exc):
        log.info("Relay socket closed")

async def start_udp_relay(host: str, port: int):
    loop = asyncio.get_running_loop()
    try:
        transport, protocol = await loop.create_datagram_endpoint(
            lambda: MultiplayerUdpRelay(),
            local_addr=(host, port)
        )
        log.info("Started Multiplayer UDP Relay on %s:%d", host, port)
        return transport
    except Exception as e:
        log.error("Could not start UDP Relay on %s:%d: %s", host, port, e)
        return None

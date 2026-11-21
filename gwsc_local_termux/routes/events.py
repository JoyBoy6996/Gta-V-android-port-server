import asyncio
import json
import logging
import time
from typing import Any, Dict, Optional, Set

from fastapi import APIRouter, Header, Query, Request
from fastapi.responses import StreamingResponse

from .. import config
from ..auth import extract_bearer, get_token_record
from ..errors import api_error

log = logging.getLogger("gwsc.events")
router = APIRouter(tags=["events"])


class EventBus:
    """Per-account SSE fan-out.

    There is deliberately no global listener set: an unauthenticated or
    misrouted connection must never receive another account's events.
    """

    def __init__(self) -> None:
        self._listeners: Dict[int, Set[asyncio.Queue]] = {}

    def subscribe(self, user_id: int) -> asyncio.Queue:
        queue: asyncio.Queue = asyncio.Queue(maxsize=100)
        self._listeners.setdefault(user_id, set()).add(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue, user_id: int) -> None:
        listeners = self._listeners.get(user_id)
        if listeners:
            listeners.discard(queue)
            if not listeners:
                self._listeners.pop(user_id, None)

    def connection_count(self, user_id: int) -> int:
        return len(self._listeners.get(user_id, set()))

    def total_connections(self) -> int:
        return sum(len(v) for v in self._listeners.values())

    async def broadcast(self, event: str, data: Any, user_id: Optional[int] = None) -> None:
        if user_id is None:
            log.warning("Refusing to broadcast SSE event %s without a target account", event)
            return
        payload = f"event: {event}\ndata: {json.dumps(data, separators=(',', ':'))}\n\n"
        for queue in list(self._listeners.get(user_id, set())):
            try:
                queue.put_nowait(payload)
            except asyncio.QueueFull:
                # Drop the event for a slow client rather than blocking the
                # request that produced it.
                pass

    def reset(self) -> None:
        self._listeners.clear()


event_bus = EventBus()


@router.get("/events")
@router.get("/gwsc/api/events")
async def events_endpoint(
    request: Request,
    token: Optional[str] = Query(None),
    authorization: Optional[str] = Header(None),
):
    raw_token = token or extract_bearer(authorization)
    record = get_token_record(raw_token)
    if not record:
        raise api_error(401, "unauthorized", "A valid token is required for the event stream")

    user_id = record["user_id"]
    if event_bus.connection_count(user_id) >= config.SSE_MAX_CONNECTIONS_PER_USER:
        raise api_error(429, "too_many_connections", "Too many event stream connections")

    async def event_generator():
        queue = event_bus.subscribe(user_id)
        try:
            yield ": connected\n\n"
            while True:
                if await request.is_disconnected():
                    break
                try:
                    payload = await asyncio.wait_for(
                        queue.get(), timeout=config.SSE_HEARTBEAT_SECONDS
                    )
                    yield payload
                except asyncio.TimeoutError:
                    yield f": ping {int(time.time())}\n\n"
        finally:
            event_bus.unsubscribe(queue, user_id)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-store",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )

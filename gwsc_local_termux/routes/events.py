import asyncio
import json
import logging
from typing import Dict, Set, Optional, Any
from fastapi import APIRouter, Request, Query, Header
from fastapi.responses import StreamingResponse
from ..auth import get_user_by_token

log = logging.getLogger("gwsc.events")
router = APIRouter(tags=["events"])

class EventBus:
    def __init__(self):
        self._listeners: Dict[int, Set[asyncio.Queue]] = {}
        self._global_listeners: Set[asyncio.Queue] = set()

    def subscribe(self, user_id: Optional[int] = None) -> asyncio.Queue:
        q = asyncio.Queue()
        if user_id is not None:
            if user_id not in self._listeners:
                self._listeners[user_id] = set()
            self._listeners[user_id].add(q)
        else:
            self._global_listeners.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue, user_id: Optional[int] = None):
        if user_id is not None and user_id in self._listeners:
            self._listeners[user_id].discard(q)
            if not self._listeners[user_id]:
                del self._listeners[user_id]
        self._global_listeners.discard(q)

    async def broadcast(self, event: str, data: Any, user_id: Optional[int] = None):
        payload = f"event: {event}\ndata: {json.dumps(data)}\n\n"
        targets = set(self._global_listeners)
        if user_id is not None and user_id in self._listeners:
            targets.update(self._listeners[user_id])
        for q in targets:
            try:
                q.put_nowait(payload)
            except asyncio.QueueFull:
                pass

event_bus = EventBus()

@router.get("/events")
@router.get("/gwsc/api/events")
async def events_endpoint(
    request: Request,
    token: Optional[str] = Query(None),
    authorization: Optional[str] = Header(None)
):
    auth_token = token
    if not auth_token and authorization:
        parts = authorization.split()
        if len(parts) == 2 and parts[0].lower() == "bearer":
            auth_token = parts[1]

    user_id = None
    if auth_token:
        user = get_user_by_token(auth_token)
        if user:
            user_id = user["id"]

    async def event_generator():
        q = event_bus.subscribe(user_id)
        # Send initial keepalive
        yield ": keepalive\n\n"
        try:
            while True:
                if await request.is_disconnected():
                    break
                try:
                    payload = await asyncio.wait_for(q.get(), timeout=15.0)
                    yield payload
                except asyncio.TimeoutError:
                    yield ": ping\n\n"
        finally:
            event_bus.unsubscribe(q, user_id)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        }
    )

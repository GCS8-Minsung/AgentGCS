import asyncio
from collections import defaultdict
from datetime import datetime, timezone

from fastapi import WebSocket

from app.models.schemas import AgentEvent


class WebSocketManager:
    def __init__(self) -> None:
        self._connections: dict[str, set[WebSocket]] = defaultdict(set)
        self._lock = asyncio.Lock()

    async def connect(self, user_id: str, websocket: WebSocket) -> None:
        await websocket.accept()
        async with self._lock:
            self._connections[user_id].add(websocket)

    async def disconnect(self, user_id: str, websocket: WebSocket) -> None:
        async with self._lock:
            sockets = self._connections.get(user_id)
            if not sockets:
                return
            sockets.discard(websocket)
            if not sockets:
                self._connections.pop(user_id, None)

    async def emit(
        self, user_id: str, event_type: str, payload: dict, run_id: str | None = None
    ) -> None:
        event = AgentEvent(
            event_type=event_type,
            run_id=run_id,
            timestamp=datetime.now(tz=timezone.utc),
            payload=payload,
        )
        await self._broadcast(user_id, event.model_dump(mode="json"))

    async def _broadcast(self, user_id: str, message: dict) -> None:
        async with self._lock:
            sockets = list(self._connections.get(user_id, set()))

        stale: list[WebSocket] = []
        for socket in sockets:
            try:
                await socket.send_json(message)
            except Exception:
                stale.append(socket)

        if stale:
            async with self._lock:
                for socket in stale:
                    self._connections[user_id].discard(socket)
                if not self._connections[user_id]:
                    self._connections.pop(user_id, None)


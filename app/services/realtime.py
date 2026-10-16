"""Bus de eventos en memoria para el WebSocket del tablero.

Suficiente para un piloto con un solo proceso de uvicorn. Para varios workers,
reemplazar por LISTEN/NOTIFY de PostgreSQL o Redis manteniendo la misma interfaz.
"""
import asyncio
import contextlib
import json
from datetime import UTC, datetime
from typing import Any


class EventBus:
    def __init__(self, max_queue: int = 500) -> None:
        self._subscribers: set[asyncio.Queue[str]] = set()
        self._max_queue = max_queue

    def subscribe(self) -> asyncio.Queue[str]:
        q: asyncio.Queue[str] = asyncio.Queue(maxsize=self._max_queue)
        self._subscribers.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue[str]) -> None:
        self._subscribers.discard(q)

    @property
    def n_subscribers(self) -> int:
        return len(self._subscribers)

    def publish(self, event_type: str, data: dict[str, Any]) -> None:
        msg = json.dumps({"type": event_type, "at": datetime.now(UTC).isoformat(), "data": data}, default=str)
        for q in list(self._subscribers):
            if q.full():  # cliente lento: se descarta el evento más antiguo
                with contextlib.suppress(asyncio.QueueEmpty):
                    q.get_nowait()
            q.put_nowait(msg)


bus = EventBus()

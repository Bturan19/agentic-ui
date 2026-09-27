from __future__ import annotations

import asyncio
import logging
from collections import defaultdict

logger = logging.getLogger(__name__)


class EventBus:
    """Ayni surec icindeki SSE abonelerine olay dagitan basit pub/sub.

    Cok islemli (multi-worker) kurulumda Redis pub/sub ile degistirilmelidir.
    """

    def __init__(self, max_queue: int = 500) -> None:
        self._subscribers: dict[str, set[asyncio.Queue]] = defaultdict(set)
        self._max_queue = max_queue

    def subscribe(self, channel: str) -> asyncio.Queue:
        queue: asyncio.Queue = asyncio.Queue(maxsize=self._max_queue)
        self._subscribers[channel].add(queue)
        return queue

    def unsubscribe(self, channel: str, queue: asyncio.Queue) -> None:
        self._subscribers[channel].discard(queue)
        if not self._subscribers[channel]:
            self._subscribers.pop(channel, None)

    async def publish(self, channel: str, event: dict) -> None:
        for queue in list(self._subscribers.get(channel, ())):
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                logger.warning("event bus kuyrugu dolu, olay dusuruldu: %s", event.get("type"))

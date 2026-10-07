"""Шина событий для SSE: бэкенд сам толкает изменения в открытые вкладки."""
import asyncio
import json

_subscribers: set[asyncio.Queue] = set()


def subscribe() -> asyncio.Queue:
    q: asyncio.Queue = asyncio.Queue(maxsize=100)
    _subscribers.add(q)
    return q


def unsubscribe(q: asyncio.Queue) -> None:
    _subscribers.discard(q)


def publish(kind: str, payload: dict) -> None:
    line = f"event: {kind}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"
    for q in list(_subscribers):
        try:
            q.put_nowait(line)
        except asyncio.QueueFull:
            # Вкладка не успевает читать — пусть перезапросит состояние сама
            unsubscribe(q)

"""Long polling для Telegram.

Крутится фоновой задачей внутри того же процесса, что и веб-приложение.
Это принципиально: если опрашивать в отдельном процессе, события SSE
не дойдут до открытых вкладок оператора — шина событий живёт в памяти.
"""
import asyncio
import logging

from . import adapters, core

log = logging.getLogger("polling")

_task: asyncio.Task | None = None


async def _loop(provider: str) -> None:
    adapter = adapters.get(provider)
    offset = 0

    try:
        await adapter.drop_webhook()
    except Exception as e:  # noqa: BLE001 — падать из-за этого не стоит
        log.warning("не удалось снять вебхук: %s", e)

    me = await adapter.whoami()
    name = (me.get("result") or {}).get("username", "?")
    log.info("long polling запущен, бот @%s", name)

    backoff = 1
    while True:
        try:
            updates = await adapter.get_updates(offset)
            backoff = 1
            for raw in updates:
                offset = max(offset, raw.get("update_id", 0) + 1)

                signal = adapter.parse_presence(raw)
                if signal:
                    core.apply_presence(provider, signal[0], signal[1])

                event = adapter.parse_update(raw)
                if event:
                    await core.ingest(event, adapter)
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001
            log.warning("опрос сорвался (%s), повтор через %s с", e, backoff)
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 60)


def start(provider: str = "telegram") -> bool:
    """Запускает опрос, если канал настроен. Возвращает, запустился ли."""
    global _task
    adapter = adapters.ADAPTERS.get(provider)
    if adapter is None or not adapter.is_configured():
        return False
    if not hasattr(adapter, "get_updates"):
        return False
    _task = asyncio.create_task(_loop(provider))
    return True


async def stop() -> None:
    if _task:
        _task.cancel()
        try:
            await _task
        except asyncio.CancelledError:
            pass

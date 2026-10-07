from .. import config
from .base import Attachment, InboundEvent, MessengerAdapter, SendResult
from .maxru import MaxAdapter
from .telegram import TelegramAdapter

ADAPTERS: dict[str, MessengerAdapter] = {
    "telegram": TelegramAdapter(config.TELEGRAM_TOKEN, config.TELEGRAM_WEBHOOK_SECRET),
    "max": MaxAdapter(config.MAX_TOKEN, config.MAX_WEBHOOK_SECRET),
}


def get(provider: str) -> MessengerAdapter:
    adapter = ADAPTERS.get(provider)
    if adapter is None:
        raise KeyError(f"Канал «{provider}» не подключён")
    return adapter


__all__ = ["ADAPTERS", "get", "Attachment", "InboundEvent",
           "MessengerAdapter", "SendResult", "MaxAdapter", "TelegramAdapter"]

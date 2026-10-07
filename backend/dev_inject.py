"""Прогон логики CRM без реального бота и публичного домена.

Отвечает вместо мессенджера консольная заглушка, поэтому видно и то,
что бот спрашивает в анкете, и то, что уходит клиенту.

    python dev_inject.py "ТС-14782"
    python dev_inject.py "ООО Проектстрой"
    python dev_inject.py "Ирина Кузнецова"
    python dev_inject.py "89162047710"
    python dev_inject.py "Не видит ключ после переустановки Windows"

Каждый вызов — одно сообщение от одного и того же клиента, так что
анкета проходится шаг за шагом. Другой клиент — третий аргумент.
"""
import asyncio
import sys

from app import core
from app.adapters.base import InboundEvent, SendResult
from app.db import now_ms


class ConsoleAdapter:
    """Вместо отправки в мессенджер печатает в консоль."""
    provider = "telegram"

    def is_configured(self):
        return True

    async def send_text(self, chat_id: str, text: str, reply_to=None) -> SendResult:
        print(f"\n  бот -> {chat_id}:\n  " + text.replace("\n", "\n  ") + "\n")
        return SendResult(external_message_id=f"console-{now_ms()}")


text = sys.argv[1] if len(sys.argv) > 1 else "Тестовое сообщение"
user = sys.argv[2] if len(sys.argv) > 2 else "555001"

print(f"клиент -> {text}")

asyncio.run(core.ingest(
    InboundEvent(
        provider="telegram",
        external_chat_id=user,
        external_user_id=user,
        external_message_id=f"dev-{now_ms()}",
        text=text,
        ts=now_ms(),
        display_name="Новый клиент",
        username="@test_client",
    ),
    ConsoleAdapter(),
))

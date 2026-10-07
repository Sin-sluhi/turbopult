"""Единый интерфейс мессенджера.

Ядро CRM не знает ни про Telegram, ни про MAX. Оно получает InboundEvent
и просит адаптер отправить ответ. Чтобы подключить новый канал, достаточно
написать ещё одну реализацию MessengerAdapter и зарегистрировать её.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional, Protocol


@dataclass
class Attachment:
    kind: str                      # image | video | audio | file | other
    name: str = ""
    size: Optional[int] = None
    remote_id: str = ""            # идентификатор файла на стороне мессенджера
    url: str = ""


@dataclass
class InboundEvent:
    """Входящее событие, приведённое к общему виду."""
    provider: str
    external_chat_id: str          # куда отвечать
    external_user_id: str          # кто написал
    external_message_id: str       # для защиты от повторной доставки
    text: str
    ts: int                        # unix, миллисекунды
    display_name: str = ""
    username: str = ""
    phone: str = ""
    start_payload: str = ""        # из диплинка ?start=...
    attachments: list[Attachment] = field(default_factory=list)
    # Для анкеты: на какое сообщение бота ответил клиент, какую кнопку
    # нажал и каким контактом поделился — всё это необязательно
    reply_to_id: str = ""
    button: str = ""
    button_ack: str = ""           # id нажатия, его надо подтвердить мессенджеру
    contact_phone: str = ""
    contact_name: str = ""

    @property
    def dedup_key(self) -> str:
        return f"{self.provider}:{self.external_message_id}"


@dataclass
class SendResult:
    external_message_id: str = ""


class MessengerAdapter(Protocol):
    provider: str

    def is_configured(self) -> bool:
        """Есть ли токен — без него канал просто выключен."""

    def verify(self, headers: dict) -> bool:
        """Проверка секрета вебхука. Чужие запросы отбрасываем до разбора тела."""

    def parse_update(self, raw: dict) -> Optional[InboundEvent]:
        """Сырой апдейт -> InboundEvent. None, если событие нам неинтересно."""

    async def send_text(self, chat_id: str, text: str,
                        reply_to: Optional[str] = None) -> SendResult: ...

    def parse_presence(self, raw: dict) -> Optional[tuple[str, str]]:
        """Сырой апдейт -> (external_chat_id, "in"|"left") или None.

        Мессенджеры не присылают 'печатает', но вход в диалог и выход
        из него отследить можно — этого хватает для зелёного и красного.
        """

    # Необязательно: вопрос с кнопками. Кто не умеет — ядро шлёт send_text.
    # buttons — список рядов [(подпись, данные)], ask_contact — кнопка
    # «поделиться номером» под полем ввода.
    # async def send_prompt(self, chat_id, text, buttons=None,
    #                       ask_contact=False, clear_keyboard=False) -> SendResult

    async def send_file(self, chat_id: str, path, caption: str = "",
                        kind: str = "") -> SendResult: ...

    async def register_webhook(self, url: str) -> dict: ...

    async def whoami(self) -> dict: ...

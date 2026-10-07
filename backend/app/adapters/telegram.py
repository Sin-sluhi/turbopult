"""Адаптер Telegram Bot API."""
from __future__ import annotations

from typing import Optional

import httpx

from .base import Attachment, InboundEvent, SendResult

API = "https://api.telegram.org"

# Заголовок, которым Telegram подписывает вебхук (задаётся в setWebhook)
SECRET_HEADER = "x-telegram-bot-api-secret-token"


class TelegramAdapter:
    provider = "telegram"

    def __init__(self, token: str, secret: str):
        self.token = token
        self.secret = secret

    # ---------- служебное ----------
    def is_configured(self) -> bool:
        return bool(self.token)

    def _url(self, method: str) -> str:
        return f"{API}/bot{self.token}/{method}"

    @staticmethod
    def _result(r):
        """Разбирает ответ Telegram.

        На отказ он присылает 400 с полем description — «chat not found»,
        «file is too big» и подобное. Это и надо показать оператору,
        а не голый код ошибки.
        """
        try:
            payload = r.json()
        except ValueError:
            raise RuntimeError(f"Telegram ответил неразборчиво ({r.status_code})")
        if not payload.get("ok"):
            raise RuntimeError(payload.get("description")
                               or f"Telegram отклонил запрос ({r.status_code})")
        return payload.get("result")

    def verify(self, headers: dict) -> bool:
        if not self.secret:
            return True
        got = headers.get(SECRET_HEADER) or headers.get(SECRET_HEADER.title())
        return got == self.secret

    # ---------- входящее ----------
    def parse_update(self, raw: dict) -> Optional[InboundEvent]:
        cb = raw.get("callback_query")
        if cb:
            return self._parse_button(cb)

        msg = raw.get("message") or raw.get("edited_message")
        if not msg:
            return None

        chat = msg.get("chat") or {}
        frm = msg.get("from") or {}
        text = msg.get("text") or msg.get("caption") or ""

        atts: list[Attachment] = []
        if msg.get("photo"):
            big = max(msg["photo"], key=lambda p: p.get("file_size") or 0)
            atts.append(Attachment(kind="image", name="photo.jpg",
                                   size=big.get("file_size"), remote_id=big.get("file_id", "")))
        if msg.get("document"):
            doc = msg["document"]
            atts.append(Attachment(kind="file", name=doc.get("file_name", "file"),
                                   size=doc.get("file_size"), remote_id=doc.get("file_id", "")))

        # Диплинк: /start <payload>
        payload = ""
        if text.startswith("/start"):
            parts = text.split(maxsplit=1)
            payload = parts[1] if len(parts) > 1 else ""

        name = " ".join(x for x in [frm.get("first_name"), frm.get("last_name")] if x)

        # Клиент нажал «Поделиться номером» — Telegram присылает карточку
        contact = msg.get("contact") or {}
        own = contact and str(contact.get("user_id", "")) == str(frm.get("id", ""))
        reply = msg.get("reply_to_message") or {}

        return InboundEvent(
            provider=self.provider,
            external_chat_id=str(chat.get("id", "")),
            external_user_id=str(frm.get("id", "")),
            external_message_id=str(msg.get("message_id", "")),
            text=text,
            ts=int(msg.get("date", 0)) * 1000,
            display_name=name or frm.get("username", "") or "Без имени",
            username=("@" + frm["username"]) if frm.get("username") else "",
            start_payload=payload,
            attachments=atts,
            reply_to_id=str(reply.get("message_id", "")) if (reply.get("from") or {}).get("is_bot") else "",
            contact_phone=contact.get("phone_number", "") if own else "",
            contact_name=" ".join(x for x in [contact.get("first_name"), contact.get("last_name")] if x) if own else "",
        )

    def _parse_button(self, cb: dict) -> Optional[InboundEvent]:
        msg = cb.get("message") or {}
        frm = cb.get("from") or {}
        name = " ".join(x for x in [frm.get("first_name"), frm.get("last_name")] if x)
        return InboundEvent(
            provider=self.provider,
            external_chat_id=str((msg.get("chat") or {}).get("id", "") or frm.get("id", "")),
            external_user_id=str(frm.get("id", "")),
            external_message_id="cb-" + str(cb.get("id", "")),
            text="",
            ts=0,
            display_name=name or frm.get("username", "") or "Без имени",
            username=("@" + frm["username"]) if frm.get("username") else "",
            button=cb.get("data", ""),
            button_ack=str(cb.get("id", "")),
            reply_to_id=str(msg.get("message_id", "")),
        )

    # ---------- исходящее ----------
    async def send_text(self, chat_id: str, text: str,
                        reply_to: Optional[str] = None) -> SendResult:
        body: dict = {"chat_id": chat_id, "text": text}
        if reply_to:
            body["reply_to_message_id"] = int(reply_to)

        async with httpx.AsyncClient(timeout=20) as c:
            r = await c.post(self._url("sendMessage"), json=body)

        result = self._result(r)
        return SendResult(external_message_id=str(result["message_id"]))

    async def send_prompt(self, chat_id: str, text: str, buttons=None,
                          ask_contact: bool = False,
                          clear_keyboard: bool = False) -> SendResult:
        """Вопрос с кнопками.

        Инлайн-кнопки висят под сообщением, кнопка «поделиться номером» —
        под полем ввода: Telegram не даёт совместить их в одном сообщении,
        поэтому номер просим обычной клавиатурой.
        """
        body: dict = {"chat_id": chat_id, "text": text}
        if ask_contact:
            body["reply_markup"] = {
                "keyboard": [[{"text": "📱 Поделиться номером", "request_contact": True}],
                             [{"text": "Пропустить"}, {"text": "Не сейчас"}]],
                "resize_keyboard": True, "one_time_keyboard": True,
            }
        elif buttons:
            body["reply_markup"] = {"inline_keyboard": [
                [{"text": t, "callback_data": d} for t, d in row] for row in buttons]}
        elif clear_keyboard:
            body["reply_markup"] = {"remove_keyboard": True}

        async with httpx.AsyncClient(timeout=20) as c:
            r = await c.post(self._url("sendMessage"), json=body)
        result = self._result(r)
        return SendResult(external_message_id=str(result["message_id"]))

    async def ack_button(self, ack_id: str, chat_id: str = "", message_id: str = "") -> None:
        """Гасим «часики» на нажатой кнопке и убираем кнопки, чтобы не жали дважды."""
        async with httpx.AsyncClient(timeout=20) as c:
            await c.post(self._url("answerCallbackQuery"), json={"callback_query_id": ack_id})
            if chat_id and message_id:
                await c.post(self._url("editMessageReplyMarkup"),
                             json={"chat_id": chat_id, "message_id": int(message_id),
                                   "reply_markup": {"inline_keyboard": []}})

    async def register_webhook(self, url: str) -> dict:
        body = {"url": url, "allowed_updates": ["message", "edited_message",
                                                 "my_chat_member", "callback_query"]}
        if self.secret:
            body["secret_token"] = self.secret
        async with httpx.AsyncClient(timeout=20) as c:
            r = await c.post(self._url("setWebhook"), json=body)
            return r.json()

    async def whoami(self) -> dict:
        async with httpx.AsyncClient(timeout=20) as c:
            r = await c.get(self._url("getMe"))
            return r.json()

    def parse_presence(self, raw: dict):
        """Telegram сообщает только о блокировке и разблокировке бота."""
        upd = raw.get("my_chat_member")
        if not upd:
            return None
        chat_id = str((upd.get("chat") or {}).get("id", ""))
        status = ((upd.get("new_chat_member") or {}).get("status") or "")
        if status in ("kicked", "left"):
            return chat_id, "left"
        if status == "member":
            return chat_id, "in"
        return None

    async def get_updates(self, offset: int, timeout: int = 25) -> list[dict]:
        """Long polling: держит соединение до появления событий.

        Домен и сертификат для этого не нужны — запрос инициируем мы сами.
        Для Telegram это законный рабочий режим, в отличие от MAX, где
        long polling годится только для разработки.
        """
        params = {
            "timeout": timeout,
            "allowed_updates": '["message","edited_message","my_chat_member","callback_query"]',
        }
        if offset:
            params["offset"] = offset
        async with httpx.AsyncClient(timeout=timeout + 10) as c:
            r = await c.get(self._url("getUpdates"), params=params)
        return self._result(r) or []

    async def drop_webhook(self) -> dict:
        """Вебхук и long polling взаимоисключающи — снимаем перед опросом."""
        async with httpx.AsyncClient(timeout=20) as c:
            r = await c.post(self._url("deleteWebhook"))
            return r.json()

    async def download_file(self, remote_id: str) -> tuple[bytes, str]:
        """Скачивает вложение: сначала узнаём путь, потом забираем файл.

        Telegram не отдаёт файл по file_id напрямую — нужен getFile,
        и только он возвращает путь, живущий около часа.
        """
        async with httpx.AsyncClient(timeout=60) as c:
            r = await c.get(self._url("getFile"), params={"file_id": remote_id})
            path = self._result(r)["file_path"]
            f = await c.get(f"{API}/file/bot{self.token}/{path}")
            f.raise_for_status()

        return f.content, path.rsplit("/", 1)[-1]

    async def send_file(self, chat_id: str, path, caption: str = "",
                        kind: str = "") -> SendResult:
        """Отправляет файл клиенту.

        Картинку шлём как фото — она раскрывается прямо в чате, а не
        приходит вложением, которое надо скачивать.
        """
        is_image = kind == "image"
        method = "sendPhoto" if is_image else "sendDocument"
        field = "photo" if is_image else "document"

        data: dict = {"chat_id": chat_id}
        if caption:
            data["caption"] = caption[:1024]

        with open(path, "rb") as fh:
            files = {field: (path.name, fh.read())}

        async with httpx.AsyncClient(timeout=120) as c:
            r = await c.post(self._url(method), data=data, files=files)

        result = self._result(r)
        return SendResult(external_message_id=str(result["message_id"]))

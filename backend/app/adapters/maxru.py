"""Адаптер MAX Bot API (platform-api2.max.ru).

Отличия от Telegram, из-за которых адаптер вообще нужен:
  * токен идёт в заголовке Authorization, а не в пути;
  * получатель задаётся query-параметром user_id или chat_id;
  * секрет вебхука приходит в X-Max-Bot-Api-Secret;
  * подписка на события — POST /subscriptions, а не setWebhook;
  * ограничение: не больше 2 сообщений в секунду в один диалог.
"""
from __future__ import annotations

from typing import Optional

import httpx

from .base import Attachment, InboundEvent, SendResult

API = "https://platform-api2.max.ru"

SECRET_HEADER = "x-max-bot-api-secret"

# Что нас интересует из полного списка событий MAX
UPDATE_TYPES = ["message_created", "bot_started", "bot_stopped", "dialog_removed"]


class MaxAdapter:
    provider = "max"

    def __init__(self, token: str, secret: str):
        self.token = token
        self.secret = secret

    # ---------- служебное ----------
    def is_configured(self) -> bool:
        return bool(self.token)

    @property
    def _headers(self) -> dict:
        return {"Authorization": self.token, "Content-Type": "application/json"}

    def verify(self, headers: dict) -> bool:
        if not self.secret:
            return True
        return headers.get(SECRET_HEADER) == self.secret

    # ---------- входящее ----------
    def parse_update(self, raw: dict) -> Optional[InboundEvent]:
        kind = raw.get("update_type")

        if kind == "bot_started":
            user = raw.get("user") or {}
            return InboundEvent(
                provider=self.provider,
                external_chat_id=str(raw.get("chat_id", "") or user.get("user_id", "")),
                external_user_id=str(user.get("user_id", "")),
                external_message_id=f"started-{raw.get('timestamp', '')}",
                text="",
                ts=int(raw.get("timestamp") or 0),
                display_name=self._name(user),
                username=("@" + user["username"]) if user.get("username") else "",
                start_payload=raw.get("payload", "") or "",
            )

        if kind != "message_created":
            return None

        msg = raw.get("message") or {}
        sender = msg.get("sender") or {}
        recipient = msg.get("recipient") or {}
        body = msg.get("body") or {}

        # В личном диалоге отвечаем в chat_id, если он пришёл, иначе по user_id
        chat_id = recipient.get("chat_id") or sender.get("user_id") or ""

        atts: list[Attachment] = []
        for a in body.get("attachments") or []:
            payload = a.get("payload") or {}
            atts.append(Attachment(
                kind=a.get("type", "other"),
                name=payload.get("filename", "") or a.get("type", "вложение"),
                size=payload.get("size"),
                remote_id=str(payload.get("token", "") or payload.get("file_id", "")),
                url=payload.get("url", "") or "",
            ))

        return InboundEvent(
            provider=self.provider,
            external_chat_id=str(chat_id),
            external_user_id=str(sender.get("user_id", "")),
            external_message_id=str(body.get("mid", "")),
            text=body.get("text") or "",
            ts=int(msg.get("timestamp") or raw.get("timestamp") or 0),
            display_name=self._name(sender),
            username=("@" + sender["username"]) if sender.get("username") else "",
            attachments=atts,
        )

    @staticmethod
    def _name(user: dict) -> str:
        name = user.get("name") or " ".join(
            x for x in [user.get("first_name"), user.get("last_name")] if x
        )
        return name or user.get("username", "") or "Без имени"

    # ---------- исходящее ----------
    async def send_text(self, chat_id: str, text: str,
                        reply_to: Optional[str] = None) -> SendResult:
        body: dict = {"text": text[:4000]}
        if reply_to:
            body["link"] = {"type": "reply", "mid": reply_to}

        async with httpx.AsyncClient(timeout=20) as c:
            r = await c.post(f"{API}/messages", params={"chat_id": chat_id},
                             headers=self._headers, json=body)
            r.raise_for_status()
            data = r.json()

        mid = ((data.get("message") or {}).get("body") or {}).get("mid", "")
        return SendResult(external_message_id=str(mid))

    async def register_webhook(self, url: str) -> dict:
        body: dict = {"url": url, "update_types": UPDATE_TYPES}
        if self.secret:
            body["secret"] = self.secret
        async with httpx.AsyncClient(timeout=20) as c:
            r = await c.post(f"{API}/subscriptions", headers=self._headers, json=body)
            return r.json()

    async def whoami(self) -> dict:
        async with httpx.AsyncClient(timeout=20) as c:
            r = await c.get(f"{API}/me", headers=self._headers)
            return r.json()

    def parse_presence(self, raw: dict):
        kind = raw.get("update_type")
        user = raw.get("user") or {}
        chat_id = str(raw.get("chat_id", "") or user.get("user_id", ""))
        if not chat_id:
            return None
        if kind == "bot_started":
            return chat_id, "in"
        if kind in ("bot_stopped", "dialog_removed"):
            return chat_id, "left"
        return None

    async def send_file(self, chat_id: str, path, caption: str = "",
                        kind: str = "") -> SendResult:
        """В MAX загрузка двухшаговая: POST /uploads, затем токен в attachments.

        Не реализовано до появления токена MAX — проверить схему на живом
        боте сейчас невозможно, а писать наугад в код, который отправляет
        файлы клиентам, не стоит.
        """
        raise RuntimeError(
            "Отправка файлов в MAX ещё не подключена: нужен токен бота, "
            "чтобы проверить двухшаговую загрузку через POST /uploads"
        )

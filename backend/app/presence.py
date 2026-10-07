"""Кружок присутствия клиента: зелёный, оранжевый, красный.

Правило, заданное заказчиком:
    зелёный   — клиент писал меньше 5 минут назад;
    оранжевый — видно, что клиент печатает (нужен источник этого события);
    красный   — больше 5 минут тишины либо клиент вышел (заблокировал бота).

ВАЖНО про реальные мессенджеры. Bot API Telegram и MAX не присылают
событие «пользователь печатает» и не сообщают, открыт ли у него чат.
Поэтому сигналы приходится выводить из того, что реально есть:

    вход в чат  -> bot_started (MAX), первое сообщение после паузы;
    выход       -> bot_stopped, dialog_removed (MAX);
    активность  -> входящее сообщение.

Функция ниже работает от абстрактных сигналов, а не от конкретного
мессенджера: как только появится источник события «печатает» — например,
мини-приложение или веб-виджет, — достаточно вызвать mark_typing().
"""
from __future__ import annotations

from . import db

FIVE_MIN_MS = 5 * 60 * 1000

GREEN = "green"
ORANGE = "orange"
RED = "red"


def state_for(conv: dict, now: int | None = None) -> dict:
    """Считаем только по тому, что знаем наверняка.

    Мессенджер не сообщает, открыт ли чат, поэтому «в чате» — это
    «писал меньше 5 минут назад». Дольше — клиент уже не в чате, и
    отсчитывается время с последней активности, а не «в чате N минут».
    """
    now = now or db.now_ms()
    st = conv.get("presence_state") or "left"
    since = conv.get("presence_since") or 0
    typing_at = conv.get("typing_at") or 0
    elapsed = now - since

    if not since:
        return {"dot": RED, "reason": "не в чате"}
    if st == "left":
        return {"dot": RED, "reason": "вышел из чата"}
    # «Печатает» актуально минуту — дольше индикатор не живёт
    if now - typing_at < 60_000:
        return {"dot": ORANGE, "reason": "печатает"}
    if elapsed < FIVE_MIN_MS:
        return {"dot": GREEN, "reason": "в чате, писал недавно"}
    return {"dot": RED, "reason": "не в чате, давно не писал"}


def mark_entered(conv_id: int) -> None:
    db.execute(
        "UPDATE conversations SET presence_state = 'in', presence_since = ? WHERE id = ?",
        (db.now_ms(), conv_id),
    )


def mark_left(conv_id: int) -> None:
    db.execute(
        "UPDATE conversations SET presence_state = 'left', presence_since = ? WHERE id = ?",
        (db.now_ms(), conv_id),
    )


def mark_typing(conv_id: int) -> None:
    db.execute("UPDATE conversations SET typing_at = ? WHERE id = ?", (db.now_ms(), conv_id))

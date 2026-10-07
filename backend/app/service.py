"""Сервисная форма: итог разговора с клиентом.

Каждая запись остаётся в базе пульта (раздел «Сервисная форма» → «Итоги»)
и уходит в Google Таблицу через скрипт таблицы — тот же, что принимал
старую форму service_form_google_v6.html. Если Google не ответил, запись
помечается «не ушло» и досылается при следующей отправке или по кнопке.
"""
import asyncio
import logging
from datetime import datetime

import httpx

from . import config, db

log = logging.getLogger("uvicorn.error")
# Отправки идут по одной: иначе досылка и новая запись могут
# отправить одну и ту же строку в таблицу дважды
_send_lock = asyncio.Lock()

# Поля в том порядке и под теми именами, которые ждёт скрипт таблицы
FIELDS = ("employee", "serial", "workplace", "organization", "subscription",
          "phone", "name", "comment", "questions")


def _row(r: dict) -> dict:
    return {k: r[k] for k in ("id", "date", "time", "created_at", "sent",
                              "sent_at", "error", "author") + FIELDS}


def list_forms(limit: int = 1000) -> list[dict]:
    rows = db.query("SELECT * FROM service_forms ORDER BY id DESC LIMIT ?", (limit,))
    return [_row(r) for r in rows]


def get(form_id: int) -> dict | None:
    r = db.one("SELECT * FROM service_forms WHERE id = ?", (form_id,))
    return _row(r) if r else None


def create(payload: dict) -> dict:
    data = {k: str(payload.get(k) or "").strip() for k in FIELDS}
    if not data["employee"]:
        raise ValueError("Выберите сотрудника")
    if not any(data[k] for k in FIELDS if k != "employee"):
        raise ValueError("Форма пустая — заполните хотя бы одно поле")
    now = datetime.now()
    form_id = db.execute(
        f"""INSERT INTO service_forms (date, time, created_at, author, {", ".join(FIELDS)})
            VALUES (?, ?, ?, ?, {", ".join("?" for _ in FIELDS)})""",
        (now.strftime("%d.%m.%Y"), now.strftime("%H:%M:%S"), db.now_ms(),
         str(payload.get("author") or ""), *(data[k] for k in FIELDS)),
    )
    return get(form_id)


async def _post(form: dict) -> None:
    """Отправка в скрипт таблицы. Google с этой сети открывается напрямую,
    поэтому сначала идём без VPN-прокси; не вышло соединиться — пробуем
    через прокси из окружения."""
    data = {"date": form["date"], "time": form["time"], **{k: form[k] for k in FIELDS}}
    last: Exception | None = None
    for trust_env in (False, True):
        try:
            async with httpx.AsyncClient(timeout=25, trust_env=trust_env,
                                         follow_redirects=True) as c:
                r = await c.post(config.SERVICE_FORM_URL, data=data)
            if r.status_code >= 400:
                raise RuntimeError(f"Google ответил {r.status_code}")
            return
        except httpx.TransportError as e:
            last = e
    raise RuntimeError(f"Google недоступен: {last.__class__.__name__}")


async def send(form_id: int) -> dict:
    if not get(form_id):
        raise LookupError("Запись не найдена")
    async with _send_lock:
        form = get(form_id)
        if form["sent"]:
            return form
        return await _send(form)


async def _send(form: dict) -> dict:
    form_id = form["id"]
    if not config.SERVICE_FORM_URL:
        db.execute("UPDATE service_forms SET error = ? WHERE id = ?",
                   ("Не задан адрес Google Таблицы (SERVICE_FORM_URL)", form_id))
        return get(form_id)
    try:
        await _post(form)
        db.execute("UPDATE service_forms SET sent = 1, sent_at = ?, error = '' WHERE id = ?",
                   (db.now_ms(), form_id))
    except Exception as e:   # оператор должен видеть причину словами
        log.warning("сервисная форма %s не ушла в Google: %s", form_id, e)
        db.execute("UPDATE service_forms SET error = ? WHERE id = ?", (str(e), form_id))
    return get(form_id)


async def send_pending(limit: int = 10) -> None:
    """Досылаем то, что раньше не ушло (по одной, от старых к новым)."""
    rows = db.query("SELECT id FROM service_forms WHERE sent = 0 ORDER BY id LIMIT ?", (limit,))
    for r in rows:
        await send(r["id"])

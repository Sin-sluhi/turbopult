"""Сервисная форма: итог разговора с клиентом.

Каждая запись остаётся в базе пульта (раздел «Сервисная форма» → «Итоги»)
и уходит в Google Таблицу через скрипт таблицы — тот же, что принимал
старую форму service_form_google_v6.html. Если Google не ответил, запись
помечается «не ушло» и досылается при следующей отправке или по кнопке.
"""
import asyncio
import base64
import json
import logging
import re
from datetime import datetime

import httpx

from . import config, db, moods

log = logging.getLogger("uvicorn.error")
# Отправки идут по одной: иначе досылка и новая запись могут
# отправить одну и ту же строку в таблицу дважды
_send_lock = asyncio.Lock()

# Поля в том порядке и под теми именами, которые ждёт скрипт таблицы
FIELDS = ("employee", "serial", "workplace", "organization", "subscription",
          "phone", "name", "comment", "questions")


def _row(r: dict) -> dict:
    return {k: r[k] for k in ("id", "date", "time", "created_at", "sent",
                              "sent_at", "error", "author", "mood") + FIELDS}


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
    # Каким был клиент: хранится только в пульте, в таблицу не отправляется
    # можно несколько пометок: список или строка через запятую
    raw = payload.get("mood") or []
    if isinstance(raw, str):
        raw = raw.split(",")
    picked = []
    for m in raw:
        m = str(m).strip()
        if not m or m in picked:
            continue
        if not moods.is_valid(m):
            raise ValueError("Неизвестная пометка о клиенте")
        picked.append(m)
    mood = ",".join(picked)
    now = datetime.now()
    form_id = db.execute(
        f"""INSERT INTO service_forms (date, time, created_at, author, mood, {", ".join(FIELDS)})
            VALUES (?, ?, ?, ?, ?, {", ".join("?" for _ in FIELDS)})""",
        (now.strftime("%d.%m.%Y"), now.strftime("%H:%M:%S"), db.now_ms(),
         str(payload.get("author") or ""), mood, *(data[k] for k in FIELDS)),
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


# ---------------------------------------------------------------- настройка формы
# Темы (разделы и пункты) и фото сотрудников общие для всех операторов,
# поэтому живут в базе, а не в браузере.
_COLOR = re.compile(r"^#[0-9a-fA-F]{6}$")
_IMAGE = re.compile(r"^data:image/(png|jpeg|webp);base64,([A-Za-z0-9+/=]+)$")
MAX_AVATAR = 400 * 1024


def _get(key: str):
    r = db.one("SELECT value FROM settings WHERE key = ?", (key,))
    return db.loads(r["value"], None) if r else None


def _set(key: str, value) -> None:
    db.execute(
        """INSERT INTO settings (key, value, updated_at) VALUES (?, ?, ?)
           ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at""",
        (key, json.dumps(value, ensure_ascii=False), db.now_ms()),
    )


def get_config() -> dict:
    """topics: null — темы ещё не меняли, интерфейс берёт стандартные."""
    avatars = {}
    for r in db.query("SELECT key, value FROM settings WHERE key LIKE 'avatar:%'"):
        avatars[r["key"][7:]] = db.loads(r["value"], "")
    return {"topics": _get("sf_topics"), "avatars": avatars}


def _text(v, limit: int, what: str, comma_ok: bool = False) -> str:
    v = str(v or "").strip()
    if not v:
        raise ValueError(f"Пустое название: {what}")
    if len(v) > limit:
        raise ValueError(f"Слишком длинное название: {v[:30]}…")
    if "," in v and not comma_ok:
        # темы пишутся в таблицу через запятую — запятая разобьёт пункт на два
        raise ValueError(f"Запятая в названии «{v}» — уберите её")
    return v


def save_topics(groups) -> list:
    if groups is None:          # «вернуть как было»
        db.execute("DELETE FROM settings WHERE key = 'sf_topics'")
        return None
    if not isinstance(groups, list) or len(groups) > 30:
        raise ValueError("Неверный список разделов")
    out, keys, texts = [], set(), set()
    for g in groups:
        name = _text(g.get("n"), 60, "раздел", comma_ok=True)
        color = str(g.get("c") or "")
        if not _COLOR.match(color):
            raise ValueError(f"Неверный цвет у раздела «{name}»")
        items = g.get("items") or []
        if not isinstance(items, list) or len(items) > 60:
            raise ValueError(f"Слишком много пунктов в разделе «{name}»")
        clean = []
        for it in items:
            n = _text(it.get("n"), 80, "пункт", comma_ok=True)
            t = _text(it.get("t") or n, 120, "пункт")
            k = str(it.get("k") or "")[:40] or f"i{len(keys)}"
            if k in keys:
                k = f"{k}_{len(keys)}"
            if t.lower() in texts:
                raise ValueError(f"Пункт «{n}» встречается дважды")
            keys.add(k); texts.add(t.lower())
            clean.append({"k": k, "n": n, "t": t})
        gk = str(g.get("k") or "")[:40] or f"g{len(out)}"
        out.append({"k": gk, "n": name, "c": color.lower(), "items": clean})
    _set("sf_topics", out)
    return out


def save_avatar(employee: str, image: str) -> dict:
    employee = str(employee or "").strip()
    if not employee or len(employee) > 40:
        raise ValueError("Не указан сотрудник")
    if not image:
        db.execute("DELETE FROM settings WHERE key = ?", ("avatar:" + employee,))
        return get_config()
    m = _IMAGE.match(image)
    if not m:
        raise ValueError("Нужна картинка PNG, JPEG или WEBP")
    if len(base64.b64decode(m.group(2))) > MAX_AVATAR:
        raise ValueError("Картинка слишком большая")
    _set("avatar:" + employee, image)
    return get_config()

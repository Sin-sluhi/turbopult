"""Анкета — параллельно с вопросом, а не перед ним.

Раньше клиент не мог задать вопрос, пока не ответит на четыре пункта, и
многие бросали на полпути. Теперь порядок обратный:

  1. клиент пишет вопрос — он сразу попадает в CRM, специалист его видит;
  2. бот отвечает «вопрос у специалиста» и, пока тот читает, предлагает
     пару коротких пунктов — с кнопками, любой можно пропустить;
  3. ответы ложатся в карточку клиента, в переписке видна строка
     «Анкета · …», а сам вопрос никто не держит в заложниках.

Как отличить ответ на анкету от продолжения вопроса: ответ на сообщение
бота (reply) или кнопка — точно анкета; обычное сообщение засчитываем,
только если оно однозначно похоже на ответ (голый номер ключа, телефон,
короткое название). Всё остальное — просто сообщение клиента, бот молчит
и не переспрашивает.

Заполненная анкета живёт у человека: второй раз её не предлагают.
Анкету перестаём задавать, как только ответил живой специалист.
"""
from __future__ import annotations

import re

from . import db, events

NO_KEY = "нет ключа"
_NO_KEY_ANSWERS = {"нет ключа", "нет", "нету", "без ключа", "не знаю", "-", "—"}
SKIP_WORDS = {"пропустить", "skip", "/skip", "дальше"}
LATER_WORDS = {"не сейчас", "потом", "отмена", "стоп", "/cancel", "cancel", "/later"}


# ---------------------------------------------------------------- проверки
def check_key(text: str):
    """Номер ключа: голое число или с префиксом ТС."""
    raw = text.strip()
    if raw.lower() in _NO_KEY_ANSWERS:
        return NO_KEY, None
    if len(raw) > 24:
        return None, "Пришлите только номер ключа — например 14782."
    digits = re.sub(r"\D", "", raw)
    if not 3 <= len(digits) <= 12:
        return None, ("Не похоже на номер ключа. Он на наклейке ключа, например 14782. "
                      "Если ключа нет — нажмите «Нет ключа».")
    has_prefix = bool(re.match(r"^\s*(ТС|TC|тс|tc)", raw))
    return (f"ТС-{digits}" if has_prefix else digits), None


def check_org(text: str):
    value = text.strip()
    if len(value) < 2:
        return None, "Слишком коротко — напишите название организации или «частное лицо»."
    return value[:120], None


def check_fio(text: str):
    parts = [p for p in re.split(r"\s+", text.strip()) if p]
    if len(parts) < 2:
        return None, "Напишите имя и фамилию — например: Ирина Кузнецова."
    if not all(re.fullmatch(r"[A-Za-zА-Яа-яЁё\-]{2,}", p) for p in parts[:2]):
        return None, "Напишите просто: Ирина Кузнецова."
    return " ".join(p.capitalize() for p in parts[:3])[:80], None


def check_phone(text: str):
    digits = re.sub(r"\D", "", text)
    if len(digits) == 11 and digits[0] in "78":
        digits = "7" + digits[1:]
    elif len(digits) == 10:
        digits = "7" + digits
    else:
        return None, "Нужен мобильный из 11 цифр, например +7 916 204-77-10."
    return f"+{digits[0]} {digits[1:4]} {digits[4:7]}-{digits[7:9]}-{digits[9:]}", None


# Похоже ли обычное (не reply) сообщение на ответ этого шага.
# Здесь лучше промолчать, чем принять продолжение вопроса за анкету.
def _looks_key(t):   return t.lower() in _NO_KEY_ANSWERS or bool(re.fullmatch(r"\s*((ТС|TC|тс|tc)[\s\-№]*)?\d{3,12}\s*", t))
def _looks_phone(t): return bool(re.fullmatch(r"[\d\s+\-()]{10,20}", t.strip()))
def _looks_fio(t):   return bool(re.fullmatch(r"\s*[A-Za-zА-Яа-яЁё\-]{2,}(\s+[A-Za-zА-Яа-яЁё\-]{2,}){1,2}\s*", t))


def _looks_org(t):
    t = t.strip()
    return (len(t) <= 60 and "\n" not in t and "?" not in t
            and len(t.split()) <= 6 and not t.endswith("!"))


# ---------------------------------------------------------------- сценарий
STEPS = [
    {"field": "license", "label": "Ключ", "check": check_key, "looks": _looks_key,
     "ask": "Номер ключа или лицензии? Он на наклейке ключа и в «Справка → О программе».",
     "extra": [("Нет ключа", "ik:v:нет ключа")]},
    {"field": "org", "label": "Организация", "check": check_org, "looks": _looks_org,
     "ask": "Название организации? Для ИП — фамилия.",
     "extra": [("Частное лицо", "ik:v:частное лицо")]},
    {"field": "phone", "label": "Телефон", "check": check_phone, "looks": _looks_phone,
     "ask": "Телефон — на случай, если в мессенджере не достучимся. "
            "Проще всего нажать кнопку «Поделиться номером» внизу.",
     "contact": True},
    {"field": "display_name", "label": "Имя", "check": check_fio, "looks": _looks_fio,
     "ask": "Как к вам обращаться? Имя и фамилия."},
]

INTRO = ("Вопрос получили — специалист уже видит его и скоро ответит.\n\n"
         "Пока ждёте: {n} {word}, чтобы специалист сразу поднял вашу лицензию "
         "и не переспрашивал. Любой можно пропустить.")
DONE = "Спасибо! Всё передал специалисту — он уже в курсе."
LATER = "Хорошо, не отвлекаю. Специалист ответит здесь же."
GREETING = ("Здравствуйте! Это поддержка Турбосметчика.\n\n"
            "Просто напишите ваш вопрос — специалист увидит его сразу. "
            "Скриншот ошибки очень поможет.")


def _plural(n):
    return "короткий вопрос" if n == 1 else ("коротких вопроса" if n < 5 else "коротких вопросов")


def _contact(contact_id: int) -> dict:
    return db.one("SELECT * FROM contacts WHERE id = ?", (contact_id,)) or {}


def _pending(contact: dict) -> list[int]:
    """Шаги, которые ещё не отвечены и не пропущены."""
    mask = contact.get("intake_mask") or 0
    out = []
    for i, s in enumerate(STEPS):
        if mask & (1 << i):
            continue
        # Поле уже известно (например, из прошлой анкеты) — не спрашиваем.
        # Имя из профиля мессенджера не считается: там часто ник.
        if s["field"] != "display_name" and contact.get(s["field"]):
            continue
        out.append(i)
    return out


def _operator_replied(conv_id: int) -> bool:
    return bool(db.one("SELECT 1 FROM messages WHERE conversation_id = ? AND direction = 'out' LIMIT 1",
                       (conv_id,)))


def _note(conv_id: int, text: str) -> None:
    """Строка в переписке: оператор видит, что клиент ответил в анкете."""
    db.execute("INSERT INTO messages (conversation_id, direction, text, author, ts) "
               "VALUES (?, 'sys', ?, 'бот', ?)", (conv_id, text, db.now_ms()))
    events.publish("message", {"conversation_id": conv_id, "direction": "sys"})


async def _send(adapter, chat_id: str, text: str, **kw) -> str:
    prompt = getattr(adapter, "send_prompt", None)
    if prompt:
        res = await prompt(chat_id, text, **kw)
    else:
        # Канал без кнопок — подсказываем словами
        if kw.get("buttons") or kw.get("ask_contact"):
            text += "\n\n(Чтобы пропустить, напишите «пропустить».)"
        res = await adapter.send_text(chat_id, text)
    return res.external_message_id


async def _ask(contact_id: int, chat_id: str, i: int, adapter, intro: str = "") -> None:
    step = STEPS[i]
    contact = _contact(contact_id)
    text = (intro + "\n\n" if intro else "") + step["ask"]
    kw: dict = {}
    if step.get("contact"):
        kw["ask_contact"] = True
    else:
        row = list(step.get("extra") or [])
        # Имя из профиля с двумя словами — предлагаем подтвердить одним нажатием
        if step["field"] == "display_name":
            name = (contact.get("display_name") or "").strip()
            if _looks_fio(name):
                row.insert(0, ("Да, " + name, "ik:name"))
        kw["buttons"] = [row, [("Пропустить", "ik:skip"), ("Не сейчас", "ik:later")]] if row \
            else [[("Пропустить", "ik:skip"), ("Не сейчас", "ik:later")]]
    msg_id = await _send(adapter, chat_id, text, **kw)
    db.execute("UPDATE contacts SET intake_step = ?, intake_started = 1, intake_prompt_id = ? WHERE id = ?",
               (i, msg_id, contact_id))


async def _advance(contact_id: int, conv_id: int, chat_id: str, adapter) -> None:
    contact = _contact(contact_id)
    rest = _pending(contact)
    if not rest:
        db.execute("UPDATE contacts SET intake_done = 1, intake_prompt_id = '' WHERE id = ?", (contact_id,))
        events.publish("client", {"contact_id": contact_id})
        await _send(adapter, chat_id, DONE, clear_keyboard=True)
        return
    if _operator_replied(conv_id):
        # Живой разговор уже идёт — бот больше не встревает
        db.execute("UPDATE contacts SET intake_prompt_id = '' WHERE id = ?", (contact_id,))
        return
    await _ask(contact_id, chat_id, rest[0], adapter)


def _mark(contact_id: int, i: int) -> None:
    db.execute("UPDATE contacts SET intake_mask = intake_mask | ? WHERE id = ?", (1 << i, contact_id))


def _store(contact_id: int, conv_id: int, i: int, value: str) -> None:
    step = STEPS[i]
    db.execute(f"UPDATE contacts SET {step['field']} = ? WHERE id = ?", (value, contact_id))
    _mark(contact_id, i)
    _note(conv_id, f"Анкета · {step['label']}: {value}")
    events.publish("client", {"contact_id": contact_id})


# ---------------------------------------------------------------- вход из ядра
def active(contact: dict) -> bool:
    return bool(contact.get("intake_prompt_id")) and not contact.get("intake_done")


async def offer(contact_id: int, conv_id: int, chat_id: str, adapter) -> None:
    """После первого настоящего сообщения в обращении — предложить анкету."""
    contact = _contact(contact_id)
    if not contact or contact.get("intake_done"):
        return
    conv = db.one("SELECT intake_offered FROM conversations WHERE id = ?", (conv_id,))
    if not conv or conv["intake_offered"] or _operator_replied(conv_id):
        return
    db.execute("UPDATE conversations SET intake_offered = 1 WHERE id = ?", (conv_id,))
    rest = _pending(contact)
    if not rest:
        db.execute("UPDATE contacts SET intake_done = 1 WHERE id = ?", (contact_id,))
        return
    db.execute("UPDATE contacts SET intake_skipped = 0 WHERE id = ?", (contact_id,))
    await _ask(contact_id, chat_id, rest[0], adapter,
               intro=INTRO.format(n=len(rest), word=_plural(len(rest))))


async def capture(contact_id: int, conv_id: int, ev, adapter) -> bool:
    """Сообщение — ответ анкеты? Тогда сохраняем и не пишем его как вопрос."""
    contact = _contact(contact_id)

    # Карточку контакта принимаем всегда: это однозначно номер клиента
    if ev.contact_phone:
        phone, _ = check_phone(ev.contact_phone)
        if phone:
            _store(contact_id, conv_id, 2, phone)
        name_ok, _ = check_fio(ev.contact_name or "")
        if name_ok and not (contact.get("intake_mask", 0) & (1 << 3)):
            _store(contact_id, conv_id, 3, name_ok)
        if active(contact):
            await _advance(contact_id, conv_id, ev.external_chat_id, adapter)
        return True

    if not active(contact) or ev.attachments:
        return False

    i = contact.get("intake_step") or 0
    if i >= len(STEPS):
        return False
    step = STEPS[i]
    text = (ev.text or "").strip()
    low = text.lower()
    is_reply = bool(ev.reply_to_id) and ev.reply_to_id == contact.get("intake_prompt_id")

    if low in SKIP_WORDS:
        _mark(contact_id, i)
        await _advance(contact_id, conv_id, ev.external_chat_id, adapter)
        return True
    if low in LATER_WORDS:
        await _later(contact_id, ev.external_chat_id, adapter)
        return True

    value, error = step["check"](text)
    if is_reply:
        if error:
            await _send(adapter, ev.external_chat_id, error)
            return True
    elif error or not step["looks"](text):
        return False   # это продолжение вопроса — пусть идёт специалисту

    _store(contact_id, conv_id, i, value)
    await _advance(contact_id, conv_id, ev.external_chat_id, adapter)
    return True


async def _later(contact_id: int, chat_id: str, adapter) -> None:
    db.execute("UPDATE contacts SET intake_skipped = 1, intake_prompt_id = '' WHERE id = ?", (contact_id,))
    await _send(adapter, chat_id, LATER, clear_keyboard=True)


async def on_button(contact_id: int, conv_id: int, ev, adapter) -> None:
    ack = getattr(adapter, "ack_button", None)
    if ack:
        try:
            await ack(ev.button_ack, ev.external_chat_id, ev.reply_to_id)
        except Exception:  # noqa: BLE001 — кнопка отработает и без этого
            pass

    contact = _contact(contact_id)
    # Нажали кнопку под старым вопросом — не путаем текущий шаг
    if not active(contact) or ev.reply_to_id != contact.get("intake_prompt_id"):
        return
    i = contact.get("intake_step") or 0
    data = ev.button

    if data == "ik:later":
        await _later(contact_id, ev.external_chat_id, adapter)
        return
    if data == "ik:skip":
        _mark(contact_id, i)
    elif data == "ik:name":
        _store(contact_id, conv_id, i, contact.get("display_name") or "")
    elif data.startswith("ik:v:"):
        _store(contact_id, conv_id, i, data[5:])
    await _advance(contact_id, conv_id, ev.external_chat_id, adapter)


def status(contact: dict) -> str:
    """Для интерфейса: done | active | skipped | none."""
    if contact.get("intake_done"):
        return "done"
    if contact.get("intake_prompt_id"):
        return "active"
    if contact.get("intake_skipped"):
        return "skipped"
    return "none"

"""Бизнес-логика: приём входящих и отправка ответов.

Здесь нет ни одного упоминания конкретного мессенджера — только provider
как строка и вызовы адаптера.
"""
import json
from dataclasses import asdict

from . import adapters, config, db, events, folders, intake, moods, presence, storage
from .adapters.base import InboundEvent


def _conv_dict(row: dict) -> dict:
    row = dict(row)
    row["tags"] = db.loads(row.get("tags", "[]"), [])
    row["folders"] = db.loads(row.get("folders", "[]"), []) or ([row["folder"]] if row.get("folder") else [])
    row["presence"] = presence.state_for(row)
    row["intake_status"] = intake.status(row)
    row.pop("intake_prompt_id", None)
    return row


def upsert_contact(ev: InboundEvent) -> int:
    found = db.one(
        "SELECT id FROM contacts WHERE provider = ? AND external_user_id = ?",
        (ev.provider, ev.external_user_id),
    )
    if found:
        # Ник в мессенджере обновляем всегда, а имя — только пока клиент
        # не назвал настоящее в анкете: профиль вроде «Новый клиент» не должен
        # затирать «Ирина Кузнецова», которую клиент ввёл сам.
        db.execute("UPDATE contacts SET username = ? WHERE id = ?",
                   (ev.username, found["id"]))
        if ev.display_name:
            db.execute(
                """UPDATE contacts SET display_name = ?
                   WHERE id = ? AND intake_done = 0 AND (intake_mask & 8) = 0""",
                (ev.display_name, found["id"]),
            )
        return found["id"]

    return db.execute(
        """INSERT INTO contacts (provider, external_user_id, display_name, username, phone, created_at)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (ev.provider, ev.external_user_id, ev.display_name, ev.username, ev.phone, db.now_ms()),
    )


def upsert_conversation(ev: InboundEvent, contact_id: int) -> int:
    found = db.one(
        "SELECT id FROM conversations WHERE provider = ? AND external_chat_id = ?",
        (ev.provider, ev.external_chat_id),
    )
    if found:
        return found["id"]

    return db.execute(
        """INSERT INTO conversations
             (contact_id, provider, external_chat_id, status, topic, last_ts, created_at)
           VALUES (?, ?, ?, 'new', ?, ?, ?)""",
        (contact_id, ev.provider, ev.external_chat_id,
         ev.start_payload or "", ev.ts or db.now_ms(), db.now_ms()),
    )


async def ingest(ev: InboundEvent, adapter=None) -> dict | None:
    """Входящее из мессенджера -> запись в CRM -> событие в открытые вкладки.

    Вопрос попадает к оператору сразу. Анкету бот предлагает после, и
    только однозначные ответы на неё не пишутся в переписку как вопрос.
    """
    if db.seen_before(ev.dedup_key):
        return None

    contact_id = upsert_contact(ev)
    conv_id = upsert_conversation(ev, contact_id)

    # Пришло сообщение — значит, клиент в чате прямо сейчас
    presence.mark_entered(conv_id)

    if adapter is not None:
        if ev.button:
            await intake.on_button(contact_id, conv_id, ev, adapter)
            return None
        if ev.text.startswith("/start") and not ev.attachments:
            await adapter.send_text(ev.external_chat_id, intake.GREETING)
            return None
        if await intake.capture(contact_id, conv_id, ev, adapter):
            return None

    if not ev.text and not ev.attachments:
        return None  # служебное событие вроде bot_started без текста

    # Ссылки мессенджеров живут около часа — забираем файлы сразу
    if adapter is not None:
        for att in ev.attachments:
            await storage.save(adapter, att)

    ts = ev.ts or db.now_ms()
    msg_id = db.execute(
        """INSERT INTO messages
             (conversation_id, direction, text, attachments, external_message_id, ts)
           VALUES (?, 'in', ?, ?, ?, ?)""",
        (conv_id, ev.text,
         json.dumps([asdict(a) for a in ev.attachments], ensure_ascii=False),
         ev.external_message_id, ts),
    )
    db.execute(
        "UPDATE conversations SET unread = unread + 1, last_ts = ? WHERE id = ?",
        (ts, conv_id),
    )

    payload = {"conversation_id": conv_id, "message_id": msg_id,
               "text": ev.text, "ts": ts, "direction": "in"}
    events.publish("message", payload)

    # Вопрос уже у оператора — теперь можно вежливо предложить анкету
    if adapter is not None:
        try:
            await intake.offer(contact_id, conv_id, ev.external_chat_id, adapter)
        except Exception as e:  # noqa: BLE001 — анкета не должна ронять приём
            import logging
            logging.getLogger("intake").warning("анкета не предложена: %s", e)
    return payload


async def send_reply(conv_id: int, text: str, author: str | None = None) -> dict:
    """Ответ оператора -> мессенджер клиента."""
    conv = db.one("SELECT * FROM conversations WHERE id = ?", (conv_id,))
    if not conv:
        raise LookupError("Диалог не найден")

    adapter = adapters.get(conv["provider"])
    if not adapter.is_configured():
        raise RuntimeError(f"Канал «{conv['provider']}» не настроен: нет токена")

    result = await adapter.send_text(conv["external_chat_id"], text)

    ts = db.now_ms()
    msg_id = db.execute(
        """INSERT INTO messages
             (conversation_id, direction, text, external_message_id, delivery, author, ts)
           VALUES (?, 'out', ?, ?, 'sent', ?, ?)""",
        (conv_id, text, result.external_message_id, author or config.OPERATOR_NAME, ts),
    )

    updates = ["last_ts = ?", "unread = 0"]
    args: list = [ts]
    if conv["status"] == "new":
        updates.append("status = 'open'")
        if not conv["assignee"]:
            updates.append("assignee = ?")
            args.append(author or config.OPERATOR_NAME)
    args.append(conv_id)
    db.execute(f"UPDATE conversations SET {', '.join(updates)} WHERE id = ?", tuple(args))

    payload = {"conversation_id": conv_id, "message_id": msg_id,
               "text": text, "ts": ts, "direction": "out"}
    events.publish("message", payload)
    return payload


def list_conversations(status: str = "all", q: str = "", folder: str = "all") -> list[dict]:
    sql = """SELECT c.*, k.display_name, k.username, k.phone, k.org, k.license, k.product,
                    k.intake_done, k.intake_skipped, k.intake_prompt_id,
                    (SELECT text FROM messages m WHERE m.conversation_id = c.id
                      AND m.direction != 'sys' ORDER BY m.ts DESC LIMIT 1) AS preview,
                    (SELECT direction FROM messages m WHERE m.conversation_id = c.id
                      AND m.direction != 'sys' ORDER BY m.ts DESC LIMIT 1) AS preview_dir
             FROM conversations c
             JOIN contacts k ON k.id = c.contact_id"""
    args: list = []
    where = ["EXISTS (SELECT 1 FROM messages m WHERE m.conversation_id = c.id)"]
    if status == "all":
        where.append("c.status != 'archived'")
    else:
        where.append("c.status = ?")
        args.append(status)
    if folder != "all":
        where.append("EXISTS (SELECT 1 FROM json_each(c.folders) j WHERE j.value = ?)")
        args.append(folder)
    if q:
        where.append("""(k.display_name LIKE ? OR k.username LIKE ? OR k.org LIKE ?
                         OR k.license LIKE ? OR c.topic LIKE ? OR c.id IN
                         (SELECT conversation_id FROM messages WHERE text LIKE ?))""")
        args.extend([f"%{q}%"] * 6)
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY c.last_ts DESC LIMIT 300"
    return [_conv_dict(r) for r in db.query(sql, tuple(args))]


def get_messages(conv_id: int) -> list[dict]:
    rows = db.query(
        "SELECT * FROM messages WHERE conversation_id = ? ORDER BY ts ASC LIMIT 500",
        (conv_id,),
    )
    for r in rows:
        r["attachments"] = db.loads(r.get("attachments", "[]"), [])
    return rows


def patch_conversation(conv_id: int, fields: dict) -> dict:
    allowed = {"status", "assignee", "topic", "note", "tags", "unread"}
    sets, args = [], []
    for key, value in fields.items():
        if key not in allowed:
            continue
        sets.append(f"{key} = ?")
        args.append(json.dumps(value, ensure_ascii=False) if key == "tags" else value)
    if sets:
        args.append(conv_id)
        db.execute(f"UPDATE conversations SET {', '.join(sets)} WHERE id = ?", tuple(args))
        events.publish("conversation", {"conversation_id": conv_id, **fields})
    return _conv_dict(db.one("SELECT * FROM conversations WHERE id = ?", (conv_id,)))


def archive(conv_id: int, folder_keys, author: str | None = None,
            mood: str | None = None) -> dict:
    """Убрать обращение в архив — в одну или сразу несколько папок.

    Один разговор часто касается нескольких тем: драйвер и установка,
    АРПС и ошибка в смете. Первая выбранная папка — основная.
    Если тема не ложится ни в одну структуру — ['undef'].
    """
    if isinstance(folder_keys, str):
        folder_keys = [folder_keys]
    keys: list[str] = []
    for k in folder_keys or []:
        if not folders.is_valid(k):
            raise ValueError(f"Неизвестная папка: {k}")
        if k not in keys:
            keys.append(k)
    # «Не определённая тема» имеет смысл только сама по себе
    if len(keys) > 1 and "undef" in keys:
        keys.remove("undef")
    if not keys:
        keys = ["undef"]

    conv = db.one("SELECT * FROM conversations WHERE id = ?", (conv_id,))
    if not conv:
        raise LookupError("Обращение не найдено")

    names = ", ".join(f"«{folders.name_of(k)}»" for k in keys)
    moved = conv["status"] == "archived"
    word = "папка" if len(keys) == 1 else "папки"
    text = (f"Папки изменены → {names}" if moved
            else f"Обращение отправлено в архив → {word} {names}")

    db.execute(
        """UPDATE conversations SET status = 'archived', folder = ?, folders = ?, unread = 0,
               archived_at = CASE WHEN status = 'archived' AND archived_at > 0
                                  THEN archived_at ELSE ? END
           WHERE id = ?""",
        (keys[0], json.dumps(keys), db.now_ms(), conv_id),
    )
    db.execute(
        "INSERT INTO messages (conversation_id, direction, text, author, ts) VALUES (?, 'sys', ?, ?, ?)",
        (conv_id, text, author or config.OPERATOR_NAME, db.now_ms()),
    )

    # Пометку о манере общения ставим тут же: оператор как раз закрыл
    # разговор и помнит, как он прошёл
    if mood is not None:
        set_mood(conv["contact_id"], mood, author)
        label = moods.name_of(mood)
        db.execute(
            "INSERT INTO messages (conversation_id, direction, text, author, ts) VALUES (?, 'sys', ?, ?, ?)",
            (conv_id,
             f"Клиент помечен: {label.lower()}" if label else "Пометка о клиенте снята",
             author or config.OPERATOR_NAME, db.now_ms()),
        )

    events.publish("conversation", {"conversation_id": conv_id,
                                    "status": "archived", "folders": keys})
    return _conv_dict(db.one("SELECT * FROM conversations WHERE id = ?", (conv_id,)))


def archive_summary() -> list[dict]:
    """Сколько обращений лежит в каждой папке — для списка папок в интерфейсе."""
    rows = db.query(
        """SELECT j.value AS folder, COUNT(*) AS n
           FROM conversations c, json_each(c.folders) j
           WHERE c.status = 'archived' GROUP BY j.value"""
    )
    counts = {r["folder"]: r["n"] for r in rows}
    out = []
    for g in folders.GROUPS:
        kids = [dict(c, count=counts.get(c["key"], 0)) for c in g["children"]]
        out.append(dict(g, children=kids, count=sum(k["count"] for k in kids)))
    out.append(dict(folders.UNDEF, children=[], count=counts.get("undef", 0), color="#78716c"))
    return out


def apply_presence(provider: str, external_chat_id: str, state: str) -> None:
    conv = db.one(
        "SELECT id FROM conversations WHERE provider = ? AND external_chat_id = ?",
        (provider, external_chat_id),
    )
    if not conv:
        return
    if state == "in":
        presence.mark_entered(conv["id"])
    else:
        presence.mark_left(conv["id"])
    events.publish("presence", {"conversation_id": conv["id"], "state": state})


# ---------------------------------------------------------------- клиенты
def list_clients(q: str = "") -> list[dict]:
    """База клиентов: человек и все его обращения за всё время."""
    sql = """SELECT k.*,
                    (SELECT COUNT(*) FROM conversations c WHERE c.contact_id = k.id) AS conv_count,
                    (SELECT MAX(last_ts) FROM conversations c WHERE c.contact_id = k.id) AS last_ts
             FROM contacts k"""
    args: list = []
    if q:
        sql += """ WHERE k.display_name LIKE ? OR k.org LIKE ?
                      OR k.license LIKE ? OR k.phone LIKE ?"""
        args = [f"%{q}%"] * 4
    sql += " ORDER BY last_ts DESC LIMIT 500"
    return db.query(sql, tuple(args))


def get_client(contact_id: int) -> dict | None:
    row = db.one("SELECT * FROM contacts WHERE id = ?", (contact_id,))
    if not row:
        return None
    row["conversations"] = db.query(
        """SELECT id, topic, status, folder, folders, last_ts
           FROM conversations WHERE contact_id = ? ORDER BY last_ts DESC""",
        (contact_id,),
    )
    return row


def set_mood(contact_id: int, mood_key: str, author: str | None = None) -> dict:
    """Пометить манеру общения. Пустая строка снимает пометку."""
    if not moods.is_valid(mood_key):
        raise ValueError(f"Неизвестная пометка: {mood_key}")
    if not db.one("SELECT id FROM contacts WHERE id = ?", (contact_id,)):
        raise LookupError("Клиент не найден")

    now = db.now_ms() if mood_key else 0
    who = (author or config.OPERATOR_NAME) if mood_key else ""
    db.execute(
        "UPDATE contacts SET mood = ?, mood_at = ?, mood_by = ? WHERE id = ?",
        (mood_key, now, who, contact_id),
    )
    events.publish("client", {"contact_id": contact_id, "mood": mood_key})
    return get_client(contact_id)


async def send_file(conv_id: int, data: bytes, filename: str,
                    caption: str = "", author: str | None = None) -> dict:
    """Файл от оператора -> мессенджер клиента.

    Копию кладём к себе до отправки: если мессенджер откажет, файл уже
    не потеряется, а оператор увидит понятную ошибку.
    """
    conv = db.one("SELECT * FROM conversations WHERE id = ?", (conv_id,))
    if not conv:
        raise LookupError("Диалог не найден")

    adapter = adapters.get(conv["provider"])
    if not adapter.is_configured():
        raise RuntimeError(f"Канал «{conv['provider']}» не настроен: нет токена")

    att = storage.save_bytes(data, filename)
    path = storage.path_of(att["url"])

    try:
        result = await adapter.send_file(conv["external_chat_id"], path,
                                         caption, att["kind"])
    except Exception:
        storage.remove(att["url"])   # не оставляем мусор после неудачи
        raise

    ts = db.now_ms()
    msg_id = db.execute(
        """INSERT INTO messages
             (conversation_id, direction, text, attachments, external_message_id,
              delivery, author, ts)
           VALUES (?, 'out', ?, ?, ?, 'sent', ?, ?)""",
        (conv_id, caption, json.dumps([att], ensure_ascii=False),
         result.external_message_id, author or config.OPERATOR_NAME, ts),
    )
    db.execute("UPDATE conversations SET last_ts = ?, unread = 0 WHERE id = ?", (ts, conv_id))

    payload = {"conversation_id": conv_id, "message_id": msg_id,
               "text": caption, "ts": ts, "direction": "out", "file": att["name"]}
    events.publish("message", payload)
    return payload


def delete_conversation(conv_id: int) -> dict:
    """Удаляет обращение вместе с перепиской и файлами.

    Только из архива: пока обращение в работе, удалять его нельзя —
    слишком легко потерять живой разговор одним промахом мыши.
    Карточка клиента остаётся: человек никуда не делся.
    """
    conv = db.one("SELECT * FROM conversations WHERE id = ?", (conv_id,))
    if not conv:
        raise LookupError("Обращение не найдено")
    if conv["status"] != "archived":
        raise RuntimeError("Удалять можно только обращения из архива. "
                           "Сначала уберите обращение в архив.")

    files = 0
    for row in db.query("SELECT attachments FROM messages WHERE conversation_id = ?", (conv_id,)):
        for att in db.loads(row["attachments"], []):
            if storage.remove(att.get("url", "")):
                files += 1

    count = db.one("SELECT COUNT(*) AS n FROM messages WHERE conversation_id = ?", (conv_id,))["n"]
    db.execute("DELETE FROM messages WHERE conversation_id = ?", (conv_id,))
    db.execute("DELETE FROM conversations WHERE id = ?", (conv_id,))

    events.publish("conversation", {"conversation_id": conv_id, "deleted": True})
    return {"deleted": conv_id, "messages": count, "files": files}


def conversation_summary(conv_id: int) -> dict | None:
    """Что именно пропадёт при удалении — показываем перед подтверждением."""
    row = db.one(
        """SELECT c.id, c.topic, c.status, c.folder, c.folders, c.created_at, c.last_ts,
                  k.display_name, k.org,
                  (SELECT COUNT(*) FROM messages m WHERE m.conversation_id = c.id) AS messages
           FROM conversations c JOIN contacts k ON k.id = c.contact_id
           WHERE c.id = ?""",
        (conv_id,),
    )
    if not row:
        return None
    files = 0
    for r in db.query("SELECT attachments FROM messages WHERE conversation_id = ?", (conv_id,)):
        files += sum(1 for a in db.loads(r["attachments"], []) if a.get("url"))
    row["files"] = files
    return row

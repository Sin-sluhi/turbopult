# -*- coding: utf-8 -*-
"""Докачивает вложения, пришедшие до появления хранилища.

Ссылка мессенджера на файл живёт около часа, поэтому помогает только для
свежих сообщений — остальные останутся без файла, и это нормально.
"""
import asyncio
import json

from app import adapters, db, storage
from app.adapters.base import Attachment


async def main():
    db.connect()
    rows = db.query("SELECT id, conversation_id, attachments FROM messages WHERE attachments != '[]'")
    done = failed = 0

    for row in rows:
        atts = db.loads(row["attachments"], [])
        changed = False

        for a in atts:
            if a.get("url") or not a.get("remote_id"):
                continue

            conv = db.one("SELECT provider FROM conversations WHERE id = ?",
                          (row["conversation_id"],))
            adapter = adapters.ADAPTERS.get(conv["provider"])
            if adapter is None or not adapter.is_configured():
                continue

            tmp = Attachment(kind=a.get("kind", ""), name=a.get("name", ""),
                             size=a.get("size"), remote_id=a["remote_id"])
            await storage.save(adapter, tmp)

            if tmp.url:
                a["url"], a["name"], a["size"] = tmp.url, tmp.name, tmp.size
                changed = True
                done += 1
            else:
                failed += 1

        if changed:
            db.execute("UPDATE messages SET attachments = ? WHERE id = ?",
                       (json.dumps(atts, ensure_ascii=False), row["id"]))

    print(f"докачано: {done}, не удалось: {failed}")


asyncio.run(main())

"""Досоздание колонок в уже существующей базе.

SQLite не умеет ADD COLUMN IF NOT EXISTS, поэтому смотрим на pragma.
"""
import sqlite3

from . import folders

COLUMNS = {
    "contacts": [
        ("mood", "TEXT NOT NULL DEFAULT ''"),
        ("mood_at", "INTEGER NOT NULL DEFAULT 0"),
        ("mood_by", "TEXT NOT NULL DEFAULT ''"),
        ("intake_step", "INTEGER NOT NULL DEFAULT 0"),
        ("intake_started", "INTEGER NOT NULL DEFAULT 0"),
        ("intake_done", "INTEGER NOT NULL DEFAULT 0"),
        # id последнего вопроса анкеты: ответ «на него» — точно ответ анкеты
        ("intake_prompt_id", "TEXT NOT NULL DEFAULT ''"),
        ("intake_skipped", "INTEGER NOT NULL DEFAULT 0"),
        ("intake_mask", "INTEGER NOT NULL DEFAULT 0"),   # биты отвеченных шагов
    ],
    "conversations": [
        ("folder", "TEXT NOT NULL DEFAULT ''"),
        # все папки обращения (JSON-список); folder — первая из них, основная
        ("folders", "TEXT NOT NULL DEFAULT '[]'"),
        ("presence_state", "TEXT NOT NULL DEFAULT 'left'"),
        ("presence_since", "INTEGER NOT NULL DEFAULT 0"),
        ("typing_at", "INTEGER NOT NULL DEFAULT 0"),
        # когда убрали в архив — отсюда длительность разговора для отчётов
        ("archived_at", "INTEGER NOT NULL DEFAULT 0"),
        ("intake_offered", "INTEGER NOT NULL DEFAULT 0"),
    ],
}


def run(conn: sqlite3.Connection) -> None:
    for table, columns in COLUMNS.items():
        have = {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
        for name, decl in columns:
            if name not in have:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {decl}")

    # Старый плоский список папок -> темы нового дерева
    for old, new in folders.LEGACY.items():
        conn.execute("UPDATE conversations SET folder = ? WHERE folder = ?", (new, old))

    # Обращения, убранные ещё в одну папку, получают список из неё
    conn.execute(
        """UPDATE conversations SET folders = json_array(folder)
           WHERE folder != '' AND (folders = '[]' OR folders = '')"""
    )

    # Время архивации для старых записей берём из системной строки
    conn.execute(
        """UPDATE conversations SET archived_at = COALESCE(
             (SELECT MAX(ts) FROM messages m WHERE m.conversation_id = conversations.id
                AND m.direction = 'sys' AND m.text LIKE '%архив%'), last_ts)
           WHERE status = 'archived' AND archived_at = 0"""
    )
    conn.commit()

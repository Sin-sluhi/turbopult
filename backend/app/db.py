import json
import sqlite3
import threading
import time
from pathlib import Path

from . import config, migrate

_lock = threading.Lock()
_conn: sqlite3.Connection | None = None


def now_ms() -> int:
    return int(time.time() * 1000)


def connect() -> sqlite3.Connection:
    global _conn
    if _conn is None:
        _conn = sqlite3.connect(config.DB_PATH, check_same_thread=False)
        _conn.row_factory = sqlite3.Row
        schema = (Path(__file__).parent / "schema.sql").read_text(encoding="utf-8")
        _conn.executescript(schema)
        _conn.commit()
        migrate.run(_conn)
    return _conn


def query(sql: str, args: tuple = ()) -> list[dict]:
    with _lock:
        rows = connect().execute(sql, args).fetchall()
    return [dict(r) for r in rows]


def one(sql: str, args: tuple = ()) -> dict | None:
    rows = query(sql, args)
    return rows[0] if rows else None


def execute(sql: str, args: tuple = ()) -> int:
    with _lock:
        conn = connect()
        cur = conn.execute(sql, args)
        conn.commit()
        return cur.lastrowid


def seen_before(key: str) -> bool:
    """True, если такой апдейт уже обрабатывали — значит, это повтор доставки."""
    if not key:
        return False
    with _lock:
        conn = connect()
        try:
            conn.execute("INSERT INTO seen_updates(key, ts) VALUES (?, ?)", (key, now_ms()))
            conn.commit()
            return False
        except sqlite3.IntegrityError:
            return True


def loads(value: str, default):
    try:
        return json.loads(value)
    except (ValueError, TypeError):
        return default

"""Хранилище вложений.

Мессенджеры отдают файл по временной ссылке: в Telegram путь из getFile
живёт около часа, в MAX похоже. Поэтому файл забираем сразу и кладём к
себе — иначе через день оператор откроет обращение и увидит битую ссылку.
"""
from __future__ import annotations

import logging
import re
import uuid
from pathlib import Path

MEDIA_DIR = Path(__file__).resolve().parents[1] / "media"
MEDIA_URL = "/media"
MAX_BYTES = 20 * 1024 * 1024

log = logging.getLogger("storage")

_SAFE = re.compile(r"[^A-Za-zА-Яа-яЁё0-9._-]+")


def _safe_name(name: str) -> str:
    name = _SAFE.sub("_", (name or "").strip())[-60:]
    return name or "file"


async def save(adapter, att) -> None:
    """Скачивает вложение и проставляет att.url. Ошибку не поднимает:
    сообщение важнее файла, оператор увидит хотя бы текст."""
    if not att.remote_id or not hasattr(adapter, "download_file"):
        return
    try:
        data, remote_name = await adapter.download_file(att.remote_id)
    except Exception as e:  # noqa: BLE001
        log.warning("вложение не скачалось: %s", e)
        return

    if len(data) > MAX_BYTES:
        log.warning("вложение больше %s МБ, пропускаем", MAX_BYTES // 1024 // 1024)
        return

    MEDIA_DIR.mkdir(parents=True, exist_ok=True)
    ext = Path(remote_name).suffix.lower() or ""
    fname = f"{uuid.uuid4().hex}{ext}"
    (MEDIA_DIR / fname).write_bytes(data)

    att.url = f"{MEDIA_URL}/{fname}"
    att.size = len(data)
    if not att.name or att.name in ("photo.jpg", "вложение"):
        att.name = _safe_name(remote_name)


def save_bytes(data: bytes, filename: str) -> dict:
    """Кладёт файл оператора к себе и возвращает описание вложения."""
    MEDIA_DIR.mkdir(parents=True, exist_ok=True)
    name = _safe_name(filename)
    ext = Path(name).suffix.lower()
    fname = f"{uuid.uuid4().hex}{ext}"
    (MEDIA_DIR / fname).write_bytes(data)
    return {"kind": kind_of(name), "name": name, "size": len(data),
            "remote_id": "", "url": f"{MEDIA_URL}/{fname}"}


IMAGE_EXT = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp", ".heic"}


def kind_of(name: str) -> str:
    return "image" if Path(name).suffix.lower() in IMAGE_EXT else "file"


def path_of(url: str) -> Path | None:
    """Локальный путь по нашей ссылке. Защищает от выхода за MEDIA_DIR."""
    if not url or not url.startswith(MEDIA_URL + "/"):
        return None
    candidate = (MEDIA_DIR / url[len(MEDIA_URL) + 1:]).resolve()
    try:
        candidate.relative_to(MEDIA_DIR.resolve())
    except ValueError:
        return None
    return candidate if candidate.is_file() else None


def remove(url: str) -> bool:
    """Удаляет файл. Возвращает, удалось ли."""
    path = path_of(url)
    if not path:
        return False
    try:
        path.unlink()
        return True
    except OSError as e:  # noqa: BLE001
        log.warning("файл не удалился: %s", e)
        return False

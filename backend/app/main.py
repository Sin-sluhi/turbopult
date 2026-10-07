"""HTTP-слой: вебхуки мессенджеров и API для интерфейса оператора."""
import asyncio
import logging
from pathlib import Path

from fastapi import Body, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from . import access, adapters, analytics, config, core, db, events, folders, moods, polling, search, storage

app = FastAPI(title="Турбопульт", docs_url="/api/docs")

WEB_DIR = Path(__file__).resolve().parents[2] / "web"

storage.MEDIA_DIR.mkdir(parents=True, exist_ok=True)
app.mount(storage.MEDIA_URL, StaticFiles(directory=storage.MEDIA_DIR), name="media")


@app.on_event("startup")
async def _startup() -> None:
    db.connect()
    # Telegram опрашиваем из этого же процесса: шина событий SSE живёт
    # в памяти, из отдельного процесса обновления до вкладок не дойдут
    if config.TELEGRAM_MODE == "polling" and polling.start("telegram"):
        logging.getLogger("uvicorn.error").info("Telegram: long polling включён")


@app.on_event("shutdown")
async def _shutdown() -> None:
    await polling.stop()


# ---------------------------------------------------------------- доступ
@app.middleware("http")
async def guard(request: Request, call_next):
    """Два замка на входе: адрес и пароль. Вебхуки не трогаем."""
    path = request.url.path
    if access.is_open_path(path):
        return await call_next(request)

    ip = request.client.host if request.client else ""
    if config.TRUST_PROXY:
        forwarded = request.headers.get("x-forwarded-for", "")
        if forwarded:
            ip = forwarded.split(",")[0].strip()

    if not access.ip_allowed(ip):
        logging.getLogger("uvicorn.error").warning(
            "отказ по адресу: %s запрашивал %s", ip, path)
        return HTMLResponse(access.denied_page(ip), status_code=403)

    if not access.authorized(request.cookies.get(access.COOKIE)):
        logging.getLogger("uvicorn.error").info("нужен вход: %s -> %s", ip, path)
        if path.startswith("/api/"):
            return HTMLResponse("Нужен вход", status_code=401)
        return HTMLResponse(access.login_page(), status_code=401)

    return await call_next(request)


@app.get("/login")
def login_form():
    return HTMLResponse(access.login_page())


@app.post("/login")
async def login(request: Request):
    form = await request.form()
    if not access.check_password(str(form.get("password", ""))):
        return HTMLResponse(access.login_page("Неверный пароль"), status_code=401)
    response = RedirectResponse("/", status_code=303)
    response.set_cookie(access.COOKIE, access.issue(), max_age=30 * 24 * 3600,
                        httponly=True, samesite="lax")
    return response


# ---------------------------------------------------------------- вебхуки
@app.post("/webhook/{provider}")
async def webhook(provider: str, request: Request):
    """Единая точка входа для всех мессенджеров.

    Путь у каждого свой, но обработка одна: проверить секрет, разобрать
    апдейт адаптером, отдать ядру. MAX требует ответ 200 в пределах
    30 секунд, иначе будет повторять доставку.
    """
    try:
        adapter = adapters.get(provider)
    except KeyError:
        raise HTTPException(status_code=404, detail="Неизвестный канал")

    headers = {k.lower(): v for k, v in request.headers.items()}
    if not adapter.verify(headers):
        raise HTTPException(status_code=401, detail="Неверный секрет вебхука")

    raw = await request.json()

    signal = adapter.parse_presence(raw)
    if signal:
        core.apply_presence(provider, signal[0], signal[1])

    event = adapter.parse_update(raw)
    if event:
        await core.ingest(event, adapter)
    return {"ok": True}


# ---------------------------------------------------------------- API
@app.get("/api/health")
def health():
    return {
        "ok": True,
        "channels": {name: a.is_configured() for name, a in adapters.ADAPTERS.items()},
        "ask_engine": "claude" if analytics.claude_ready() else "local",
    }


@app.get("/tp-ping")
def ping():
    """Метка «это действительно Турбопульт». По ней скрипт ссылки проверяет,
    что туннель ведёт к нам, а не на чужой сайт с тем же адресом."""
    return HTMLResponse("turbopult-ok")


@app.get("/api/conversations")
def conversations(status: str = "all", q: str = "", folder: str = "all"):
    return core.list_conversations(status, q, folder)


@app.get("/api/conversations/{conv_id}/messages")
def messages(conv_id: int):
    return core.get_messages(conv_id)


@app.post("/api/conversations/{conv_id}/reply")
async def reply(conv_id: int, payload: dict = Body(...)):
    text = (payload.get("text") or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="Пустой текст ответа")
    try:
        return await core.send_reply(conv_id, text, payload.get("author"))
    except LookupError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except RuntimeError as e:
        raise HTTPException(status_code=409, detail=str(e))


@app.post("/api/conversations/{conv_id}/attach")
async def attach(conv_id: int, file: UploadFile = File(...), caption: str = Form("")):
    """Отправляет файл клиенту в его мессенджер."""
    data = await file.read()
    if not data:
        raise HTTPException(status_code=400, detail="Пустой файл")
    if len(data) > storage.MAX_BYTES:
        raise HTTPException(status_code=413,
                            detail=f"Файл больше {storage.MAX_BYTES // 1024 // 1024} МБ")
    try:
        return await core.send_file(conv_id, data, file.filename or "file", caption)
    except LookupError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except RuntimeError as e:
        raise HTTPException(status_code=409, detail=str(e))


@app.get("/api/conversations/{conv_id}/summary")
def summary(conv_id: int):
    """Сводка перед удалением: что именно пропадёт."""
    row = core.conversation_summary(conv_id)
    if not row:
        raise HTTPException(status_code=404, detail="Обращение не найдено")
    return row


@app.delete("/api/conversations/{conv_id}")
def delete_conversation(conv_id: int):
    """Удаляет обращение навсегда. Разрешено только из архива."""
    try:
        return core.delete_conversation(conv_id)
    except LookupError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except RuntimeError as e:
        raise HTTPException(status_code=409, detail=str(e))


@app.patch("/api/conversations/{conv_id}")
def patch(conv_id: int, payload: dict = Body(...)):
    return core.patch_conversation(conv_id, payload)


@app.get("/api/folders")
def folder_list():
    """Список папок архива со счётчиками — для окна выбора."""
    return core.archive_summary()


@app.post("/api/conversations/{conv_id}/archive")
def archive(conv_id: int, payload: dict = Body(...)):
    """Убрать обращение в архив: folders — список папок (или одна folder).
    'undef', если тема не определилась."""
    try:
        return core.archive(conv_id, payload.get("folders") or [payload.get("folder", "undef")],
                            payload.get("author"), payload.get("mood"))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except LookupError as e:
        raise HTTPException(status_code=404, detail=str(e))


@app.get("/api/archive/search")
def archive_search(q: str = "", limit: int = 40):
    """Умный поиск по архиву: пара слов вытаскивает обращения из всех папок."""
    return search.search(q, limit)


@app.get("/api/ask")
def ask(q: str = ""):
    """Вопрос к базе обычным языком: «сколько пользователей написало про драйвер в сентябре»."""
    return analytics.ask(q)


@app.get("/api/today")
def today():
    """Сводка за сегодня для экрана со свёрнутым чатом."""
    return analytics.today()


@app.get("/api/moods")
def mood_list():
    """Справочник пометок о манере общения."""
    return moods.MOODS


@app.get("/api/clients")
def clients(q: str = ""):
    return core.list_clients(q)


@app.get("/api/clients/{contact_id}")
def client(contact_id: int):
    row = core.get_client(contact_id)
    if not row:
        raise HTTPException(status_code=404, detail="Клиент не найден")
    return row


@app.post("/api/clients/{contact_id}/mood")
def client_mood(contact_id: int, payload: dict = Body(...)):
    """Пометить манеру общения. Пустая строка снимает пометку."""
    try:
        return core.set_mood(contact_id, payload.get("mood", ""), payload.get("author"))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except LookupError as e:
        raise HTTPException(status_code=404, detail=str(e))


@app.get("/api/stream")
async def stream():
    """SSE: новые сообщения прилетают в интерфейс без перезагрузки."""
    q = events.subscribe()

    async def gen():
        try:
            yield ": подключено\n\n"
            while True:
                try:
                    yield await asyncio.wait_for(q.get(), timeout=25)
                except asyncio.TimeoutError:
                    yield ": ping\n\n"   # держим соединение открытым
        finally:
            events.unsubscribe(q)

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache",
                                      "X-Accel-Buffering": "no"})


# ---------------------------------------------------------------- настройка
@app.post("/api/setup/{provider}")
async def setup(provider: str):
    """Регистрирует вебхук в мессенджере. Вызывается один раз после деплоя."""
    adapter = adapters.get(provider)
    if not adapter.is_configured():
        raise HTTPException(status_code=409, detail="Нет токена для этого канала")
    if not config.PUBLIC_URL.startswith("https://"):
        raise HTTPException(status_code=409,
                            detail="PUBLIC_URL должен быть https — оба мессенджера "
                                   "не принимают http и самоподписанные сертификаты")
    url = f"{config.PUBLIC_URL}/webhook/{provider}"
    return {"registered": url, "response": await adapter.register_webhook(url)}


@app.get("/api/setup/{provider}")
async def whoami(provider: str):
    return await adapters.get(provider).whoami()


# ---------------------------------------------------------------- интерфейс
@app.get("/")
def index():
    """Отдаёт интерфейс оператора.

    Файл интерфейса пишется без <!doctype>, потому что при публикации
    артефактом обёртку добавляет платформа. При своём хостинге её нет,
    и браузер уходит в quirks-режим, где селекторы id перестают
    различать регистр — поэтому дописываем обёртку здесь.
    """
    page = (WEB_DIR / "index.html").read_text(encoding="utf-8")
    # Отдаёт сервер — значит, демо-данные показывать нельзя ни на миг:
    # страница сразу ждёт настоящие обращения
    page = "<script>window.TP_SERVER = true;</script>\n" + page
    if not page.lstrip().lower().startswith("<!doctype"):
        page = (
            '<!doctype html>\n<html lang="ru">\n<head>\n'
            '<meta charset="utf-8">\n'
            '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
            '</head>\n<body>\n' + page + '\n</body>\n</html>'
        )
    # Не кэшировать: после обновления пульта у коллег должна открыться новая версия
    return HTMLResponse(page, headers={"Cache-Control": "no-store"})

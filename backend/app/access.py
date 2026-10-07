"""Доступ к пульту снаружи.

В CRM лежат телефоны, номера лицензий и переписка с клиентами, а сам
сервис не умеет разделять пользователей. Поэтому наружу его выпускаем
только через два замка сразу:

    1. список разрешённых адресов — отсекает случайных сканеров;
    2. пароль — потому что IP подделывается, а у сотрудника он ещё и
       может смениться вместе с перезагрузкой роутера.

Вебхуки мессенджеров из-под замка выведены: их дёргает Telegram, а не
человек, и они защищены своим секретом.
"""
from __future__ import annotations

import hashlib
import hmac
import ipaddress

from . import config

COOKIE = "tp_access"
OPEN_PATHS = ("/webhook/", "/login", "/tp-ping")


def _networks() -> list:
    nets = []
    for raw in config.ALLOW_IPS.split(","):
        raw = raw.strip()
        if not raw:
            continue
        try:
            nets.append(ipaddress.ip_network(raw, strict=False))
        except ValueError:
            continue
    return nets


ALLOWED = _networks()


OPEN_TO_ALL = "*" in [x.strip() for x in config.ALLOW_IPS.split(",")]


def ip_allowed(ip: str) -> bool:
    """Пустой список — значит, наружу не открывали: пускаем только себя.
    Звёздочка — открыто всем, единственной защитой остаётся пароль."""
    if OPEN_TO_ALL:
        return True
    if not ALLOWED:
        return ip in ("127.0.0.1", "::1", "testclient")
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return False
    return any(addr in net for net in ALLOWED)


def password_required() -> bool:
    return bool(config.ACCESS_PASSWORD)


def _token() -> str:
    """Значение куки — производная от пароля, сам пароль никуда не уходит."""
    return hmac.new(config.ACCESS_PASSWORD.encode("utf-8"),
                    b"turbopult-access", hashlib.sha256).hexdigest()


def check_password(value: str) -> bool:
    return hmac.compare_digest(value or "", config.ACCESS_PASSWORD)


def issue() -> str:
    return _token()


def authorized(cookie_value: str | None) -> bool:
    if not password_required():
        return True
    return hmac.compare_digest(cookie_value or "", _token())


def is_open_path(path: str) -> bool:
    return any(path.startswith(p) for p in OPEN_PATHS)


LOGIN_PAGE = """<!doctype html>
<html lang="ru"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Турбопульт</title>
<style>
 body{margin:0;min-height:100vh;display:grid;place-items:center;background:#070b14;
      color:#eff4fc;font:15px/1.5 system-ui,"Segoe UI",Roboto,sans-serif}
 form{width:min(360px,90vw);background:rgba(17,25,42,.8);border:1px solid rgba(255,255,255,.1);
      border-radius:24px;padding:30px;box-shadow:0 24px 60px rgba(0,0,0,.5)}
 h1{margin:0 0 6px;font-size:21px;letter-spacing:-.02em}
 p{margin:0 0 20px;color:#9db0ca;font-size:13.5px}
 input{width:100%;box-sizing:border-box;background:rgba(255,255,255,.05);color:inherit;
       border:1px solid rgba(255,255,255,.12);border-radius:14px;padding:13px 15px;font:inherit}
 input:focus{outline:2px solid #22c8f5;outline-offset:1px}
 button{width:100%;margin-top:12px;border:0;border-radius:14px;padding:14px;cursor:pointer;
        font:600 15px system-ui;color:#03202c;background:linear-gradient(150deg,#6fe6ff,#22c8f5)}
 .err{margin:14px 0 0;color:#ff6f62;font-size:13px}
</style></head><body>
<form method="post" action="/login">
  <h1>Турбопульт</h1>
  <p>Доступ к пульту поддержки</p>
  <input type="password" name="password" placeholder="Пароль" autofocus autocomplete="current-password">
  <button type="submit">Войти</button>
  __ERROR__
</form></body></html>"""


def login_page(error: str = "") -> str:
    block = f'<p class="err">{error}</p>' if error else ""
    return LOGIN_PAGE.replace("__ERROR__", block)


def denied_page(ip: str) -> str:
    """Страница отказа называет адрес, который увидел сервер.

    Без этого разбирательство превращается в гадание: у сотрудника
    адрес мог смениться, и понять это со стороны невозможно.
    """
    return LOGIN_PAGE.replace(
        "__ERROR__",
        f'<p class="err">Ваш адрес {ip} не в списке разрешённых.<br>'
        f'Передайте его администратору — он добавит доступ.</p>'
    ).replace(
        '<input type="password" name="password" placeholder="Пароль" autofocus autocomplete="current-password">', ""
    ).replace(
        '<button type="submit">Войти</button>', ""
    ).replace(
        "Доступ к пульту поддержки", "Доступ закрыт"
    )

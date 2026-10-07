import os
from dotenv import load_dotenv

load_dotenv()

PUBLIC_URL = os.getenv("PUBLIC_URL", "").rstrip("/")
DB_PATH = os.getenv("DB_PATH", "crm.db")
OPERATOR_NAME = os.getenv("OPERATOR_NAME", "Оператор")

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "")
TELEGRAM_WEBHOOK_SECRET = os.getenv("TELEGRAM_WEBHOOK_SECRET", "")

MAX_TOKEN = os.getenv("MAX_TOKEN", "")
MAX_WEBHOOK_SECRET = os.getenv("MAX_WEBHOOK_SECRET", "")

# polling — опрашиваем Telegram сами, домен не нужен (годится и для боя)
# webhook — Telegram шлёт события на PUBLIC_URL, нужен HTTPS-домен
TELEGRAM_MODE = os.getenv("TELEGRAM_MODE", "polling")

# Доступ снаружи. Пусто -> пульт виден только с этой машины.
# ALLOW_IPS: адреса или подсети через запятую, например 127.0.0.1,203.0.113.7
ALLOW_IPS = os.getenv("ALLOW_IPS", "")
ACCESS_PASSWORD = os.getenv("ACCESS_PASSWORD", "")
# TRUST_PROXY=1 — только если перед приложением стоит свой nginx:
# иначе заголовок X-Forwarded-For подделывается кем угодно
TRUST_PROXY = os.getenv("TRUST_PROXY", "") == "1"

# Строка «спросить у базы»: если есть ANTHROPIC_API_KEY, вопросы разбирает Claude
ASK_MODEL = os.getenv("ASK_MODEL", "claude-opus-5")

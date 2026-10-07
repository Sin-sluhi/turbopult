#!/usr/bin/env bash
# Разворачивает Турбопульт на чистом сервере Ubuntu 22.04/24.04.
#
#   sudo bash setup.sh pult.example.ru admin@example.ru
#
# После этого пульт живёт по https://pult.example.ru круглосуточно,
# независимо от домашнего компьютера, с сертификатом и автозапуском.

set -euo pipefail

DOMAIN="${1:-}"
EMAIL="${2:-}"
APP_DIR="/opt/turbopult"
USER_NAME="turbopult"

if [[ -z "$DOMAIN" || -z "$EMAIL" ]]; then
  echo "Использование: sudo bash setup.sh <домен> <почта для сертификата>"
  exit 1
fi
if [[ $EUID -ne 0 ]]; then
  echo "Запускать от root: sudo bash setup.sh ..."
  exit 1
fi

echo "==> 1/6 Пакеты"
apt-get update -qq
apt-get install -y -qq python3 python3-venv python3-pip nginx certbot python3-certbot-nginx rsync

echo "==> 2/6 Пользователь и каталог"
id -u "$USER_NAME" &>/dev/null || useradd --system --create-home --shell /usr/sbin/nologin "$USER_NAME"
mkdir -p "$APP_DIR"
rsync -a --exclude '.venv' --exclude '__pycache__' --exclude '*.pyc' \
      "$(dirname "$(readlink -f "$0")")/../" "$APP_DIR/"
chown -R "$USER_NAME:$USER_NAME" "$APP_DIR"

echo "==> 3/6 Окружение Python"
sudo -u "$USER_NAME" python3 -m venv "$APP_DIR/backend/.venv"
sudo -u "$USER_NAME" "$APP_DIR/backend/.venv/bin/pip" install -q --upgrade pip
sudo -u "$USER_NAME" "$APP_DIR/backend/.venv/bin/pip" install -q -r "$APP_DIR/backend/requirements.txt"

echo "==> 4/6 Служба"
cat > /etc/systemd/system/turbopult.service <<UNIT
[Unit]
Description=Турбопульт — пульт поддержки
After=network.target

[Service]
Type=simple
User=$USER_NAME
WorkingDirectory=$APP_DIR/backend
Environment=PYTHONUNBUFFERED=1
ExecStart=$APP_DIR/backend/.venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
UNIT

systemctl daemon-reload
systemctl enable --now turbopult

echo "==> 5/6 Nginx"
cat > /etc/nginx/sites-available/turbopult <<NGINX
server {
    listen 80;
    server_name $DOMAIN;

    client_max_body_size 25m;

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;

        # Поток событий не должен буферизоваться, иначе сообщения
        # будут доходить до оператора пачками с задержкой
        proxy_buffering off;
        proxy_read_timeout 3600s;
    }
}
NGINX

ln -sf /etc/nginx/sites-available/turbopult /etc/nginx/sites-enabled/turbopult
rm -f /etc/nginx/sites-enabled/default
nginx -t && systemctl reload nginx

echo "==> 6/6 Сертификат"
certbot --nginx -d "$DOMAIN" --non-interactive --agree-tos -m "$EMAIL" --redirect

echo
echo "Готово."
echo "  Пульт:   https://$DOMAIN"
echo "  Пароль:  строка ACCESS_PASSWORD в $APP_DIR/backend/.env"
echo
echo "Осталось два шага:"
echo "  1) впишите в $APP_DIR/backend/.env строки"
echo "       PUBLIC_URL=https://$DOMAIN"
echo "       TELEGRAM_MODE=webhook"
echo "       TRUST_PROXY=1"
echo "     и перезапустите: systemctl restart turbopult"
echo "  2) переведите бота на вебхук:"
echo "       curl -X POST https://$DOMAIN/api/setup/telegram"
echo
echo "Логи:      journalctl -u turbopult -f"
echo "Перезапуск: systemctl restart turbopult"

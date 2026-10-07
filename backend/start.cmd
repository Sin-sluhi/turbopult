@echo off
chcp 65001 >nul
title Турбопульт
cd /d "%~dp0"

echo.
echo   Турбопульт — пульт поддержки
echo   ----------------------------
echo.

if not exist ".venv\Scripts\python.exe" (
    echo   Первый запуск: готовлю окружение...
    where py >nul 2>nul
    if errorlevel 1 (
        echo   [!] Python не найден. Поставьте его командой:
        echo       winget install --id Python.Python.3.13 -e
        pause
        exit /b 1
    )
    py -3 -m venv .venv
    ".venv\Scripts\python.exe" -m pip install --quiet --upgrade pip
    ".venv\Scripts\python.exe" -m pip install --quiet -r requirements.txt
    echo   Окружение готово.
    echo.
)

if not exist ".env" (
    copy ".env.example" ".env" >nul
    echo   [!] Создан .env — впишите в него токен бота и перезапустите.
    pause
    exit /b 1
)

echo   Пульт: http://localhost:8000
echo   Остановить: закройте это окно или нажмите Ctrl+C
echo.

".venv\Scripts\python.exe" -m uvicorn app.main:app --host 0.0.0.0 --port 8000
pause

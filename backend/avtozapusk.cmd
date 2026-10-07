@echo off
rem Turbopult in background. Started at Windows logon by avtozapusk.vbs:
rem no window, restarts itself if the server stops.
rem Public access goes through the CloudPub tunnel, so we listen on 127.0.0.1 only.
rem Keep this file ASCII-only: cmd mis-parses goto labels in UTF-8 files.
cd /d "%~dp0"

rem Telegram API is blocked on this network directly; it works only through
rem the local VPN client (Happ, HTTP proxy on 127.0.0.1:10809). Local and
rem Russian addresses (MAX) must go direct. If the VPN app is not running,
rem the bot cannot reach Telegram, but the web panel keeps working.
set HTTPS_PROXY=http://127.0.0.1:10809
set HTTP_PROXY=http://127.0.0.1:10809
set NO_PROXY=127.0.0.1,localhost,max.ru,platform-api2.max.ru

:loop
rem Already running (e.g. started by hand) - do not start a second copy
curl -s -m 3 http://127.0.0.1:8000/tp-ping | find "turbopult-ok" >nul
if not errorlevel 1 (
    timeout /t 30 /nobreak >nul
    goto loop
)

rem Keep the log small: over 10 MB it moves to server.old.log
if exist server.log for %%A in (server.log) do if %%~zA GTR 10000000 move /y server.log server.old.log >nul

echo [%date% %time%] server start >> server.log
".venv\Scripts\python.exe" -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --log-level warning >> server.log 2>&1
echo [%date% %time%] server stopped, restart in 5 s >> server.log
timeout /t 5 /nobreak >nul
goto loop

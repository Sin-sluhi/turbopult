@echo off
rem Permanent public link via CloudPub (servers in Moscow).
rem Started at Windows logon by avtozapusk.vbs, no window, restarts if it drops.
rem Keep this file ASCII-only: cmd mis-parses goto labels in UTF-8 files.
cd /d "%~dp0"

rem Tunnel already running (started earlier) - do not start a second copy
tasklist /fi "imagename eq clo.exe" | find /i "clo.exe" >nul && exit /b

rem The VPN client sets a system proxy abroad; the tunnel must go direct
set HTTP_PROXY=
set HTTPS_PROXY=
set http_proxy=
set https_proxy=
set NO_PROXY=*

:loop
if exist cloudpub.log for %%A in (cloudpub.log) do if %%~zA GTR 10000000 move /y cloudpub.log cloudpub.old.log >nul
echo [%date% %time%] tunnel start >> cloudpub.log
"..\tools\cloudpub\clo.exe" -l warn run >> cloudpub.log 2>&1
echo [%date% %time%] tunnel stopped, restart in 10 s >> cloudpub.log
timeout /t 10 /nobreak >nul
goto loop

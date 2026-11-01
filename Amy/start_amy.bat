@echo off
setlocal
cd /d "%~dp0"
title Amy

set "PY=python"
where py >nul 2>nul && set "PY=py"

echo.
echo   Starting Amy
echo   - Discord bot + voice sidecar   (this window)
echo   - Dashboard                     http://127.0.0.1:8766/   (separate window)
echo.

rem Dashboard in its own window so the bot's logs stay readable here.
start "Amy Dashboard" cmd /k %PY% -m interfaces.dashboard

rem Wait until the dashboard actually answers, then open it in the browser.
start "" powershell -NoProfile -WindowStyle Hidden -Command "$u='http://127.0.0.1:8766/'; for($i=0;$i -lt 40;$i++){ try{ $null = Invoke-WebRequest -UseBasicParsing $u -TimeoutSec 1; break } catch { Start-Sleep -Milliseconds 250 } }; Start-Process $u"

echo   Bot is starting below. Close this window to stop the bot.
echo   Close the dashboard window to stop the dashboard.
echo.
"%PY%" -m interfaces.discord_bot
if errorlevel 1 (
    echo.
    echo The bot exited with an error. Press any key to close.
    pause >nul
)

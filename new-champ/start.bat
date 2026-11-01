@echo off
title Champ - Discord Companion
cd /d "%~dp0"

rem ---- Read settings from .env ----
set "DASHBOARD_PORT=3000"
set "DISCORD_TOKEN="
for /f "usebackq tokens=1,* delims==" %%A in (".env") do (
  if /i "%%A"=="DASHBOARD_PORT" set "DASHBOARD_PORT=%%B"
  if /i "%%A"=="DISCORD_TOKEN" set "DISCORD_TOKEN=%%B"
)
if "%DISCORD_TOKEN%"=="" (
  echo [!] DISCORD_TOKEN is empty in .env - the dashboard will run but the bot stays offline.
  start notepad .env
)

rem ---- Ollama ----
curl -s -o nul http://127.0.0.1:11434/api/tags && goto ollamaready
echo [*] Starting Ollama...
start "Ollama" /min ollama serve
:waitollama
timeout /t 1 /nobreak >nul
curl -s -o nul http://127.0.0.1:11434/api/tags || goto waitollama
:ollamaready
echo [ok] Ollama is running

rem ---- Dependencies ----
if not exist node_modules (
  echo [*] Installing dependencies...
  call npm install || (pause & exit /b 1)
)

rem ---- Slash commands (quick, keeps Discord in sync with src\commands) ----
echo [*] Registering slash commands...
call npm run deploy --silent || echo [!] Command registration failed, starting anyway.

rem ---- Dashboard opens once the bot is up ----
start "" /min cmd /c "timeout /t 8 /nobreak >nul & start http://127.0.0.1:%DASHBOARD_PORT%"

rem ---- Bot + dashboard (restarts itself if it crashes) ----
rem Turning the bot off in the dashboard keeps this running so you can turn it back on.
:run
echo [*] Starting... (close this window or press Ctrl+C to quit completely)
node src/index.js
echo.
echo [!] Bot stopped. Restarting in 5 seconds - close the window to quit.
timeout /t 5 /nobreak >nul
goto run

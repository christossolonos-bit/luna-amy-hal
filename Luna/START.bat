@echo off
REM Luna HalMini — hidden backends; Electron avatar; browser = HalMini/Amy dashboard.
REM Scope: HALMINI.md
chcp 65001 >nul
title Luna HalMini
setlocal EnableDelayedExpansion
cd /d "%~dp0"

set "RIKO_CHARACTER=luna"
set "RIKO_VOICE_SOURCE=both"
set "HALMINI_ENABLED=1"
set "HALMINI_DB=%~dp0data\luna_amy.db"
set "HALMINI_SELF_NAME=Luna"
set "HALMINI_PERSONA=amy"
set "DASHBOARD_PORT=8766"
set "DASHBOARD_URL=http://127.0.0.1:%DASHBOARD_PORT%/"

REM Prefer HalMini-share umbrella siblings (Amy / HalMini / new-champ).
set "LUNA_ROOT=%~dp0"
if "%LUNA_ROOT:~-1%"=="\" set "LUNA_ROOT=%LUNA_ROOT:~0,-1%"
for %%I in ("%LUNA_ROOT%\..") do set "HALMINI_SHARE=%%~fI"
if exist "%HALMINI_SHARE%\Amy\app\conversation.py" (
    set "HALMINI_ROOT=%HALMINI_SHARE%\Amy"
) else if exist "%HALMINI_SHARE%\HalMini\app\conversation.py" (
    set "HALMINI_ROOT=%HALMINI_SHARE%\HalMini"
) else (
    set "HALMINI_ROOT=D:\Amy\Amy"
)
if exist "%HALMINI_SHARE%\amy-smith\data\companion.db" (
    set "NEWCHAMP_IMPORT_DB=%HALMINI_SHARE%\amy-smith\data\companion.db"
) else if exist "%HALMINI_SHARE%\new-champ\data\companion.db" (
    set "NEWCHAMP_IMPORT_DB=%HALMINI_SHARE%\new-champ\data\companion.db"
)
set "NEWCHAMP_ENABLED=1"
set "NEWCHAMP_DB=%LUNA_ROOT%\data\newchamp_memories.db"

echo.
echo   Luna HalMini
echo   - Avatar: Electron ^(as before^)
echo   - Browser: HalMini dashboard  %DASHBOARD_URL%
echo   - Backends: hidden
echo.

if not exist ".venv\Scripts\python.exe" (
    echo   First run: double-click setup.bat, wait for it to finish, then run this again.
    echo.
    pause
    exit /b 1
)

if not exist ".env" (
    if exist ".env.example" copy /y ".env.example" ".env" >nul
)

echo   Stopping any previous instance...
for /f "tokens=5" %%A in ('netstat -ano ^| findstr "LISTENING" ^| findstr ":8100 "') do taskkill /PID %%A /T /F >nul 2>&1
for /f "tokens=5" %%A in ('netstat -ano ^| findstr "LISTENING" ^| findstr ":5173 "') do taskkill /PID %%A /T /F >nul 2>&1
for /f "tokens=5" %%A in ('netstat -ano ^| findstr "LISTENING" ^| findstr ":%DASHBOARD_PORT% "') do taskkill /PID %%A /T /F >nul 2>&1
taskkill /FI "WINDOWTITLE eq Luna Discord Bridge*" /T /F >nul 2>&1
taskkill /FI "WINDOWTITLE eq Luna Server*" /T /F >nul 2>&1
taskkill /FI "WINDOWTITLE eq Luna Web Page*" /T /F >nul 2>&1
taskkill /FI "WINDOWTITLE eq Luna Desktop*" /T /F >nul 2>&1
REM Only Luna backends (never Cursor / Discord app / bare Electron).
powershell -NoProfile -ExecutionPolicy Bypass -File "%LUNA_ROOT%\tools\stop_luna_procs.ps1" -LunaRoot "%LUNA_ROOT%" -AmyRoot "%HALMINI_ROOT%" -ShareRoot "%HALMINI_SHARE%" >nul 2>&1
ping -n 2 127.0.0.1 >nul

set "SERVER_PORT="
for /f "tokens=2 delims==" %%P in ('findstr /b /c:"SERVER_PORT: int =" "server\config.py"') do set "SERVER_PORT=%%P"
set "SERVER_PORT=%SERVER_PORT: =%"
if not defined SERVER_PORT set "SERVER_PORT=8100"

if not exist "client\node_modules\vite\package.json" (
    echo   Installing web page packages ^(first time^)...
    pushd client
    call npm install
    if errorlevel 1 (
        echo   ERROR: npm install failed in the client folder.
        popd
        pause
        exit /b 1
    )
    popd
)

set "DISCORD_ON=0"
if exist "discord-bridge\.env" (
    if not exist "discord-bridge\node_modules\" (
        echo   Installing discord-bridge packages...
        pushd discord-bridge
        call npm install
        popd
    )
    set "DISCORD_ON=1"
)

set "HALMINI_DB_ABS=%LUNA_ROOT%\data\luna_amy.db"

powershell -NoProfile -ExecutionPolicy Bypass -File "%LUNA_ROOT%\tools\start_hidden.ps1" -Root "%LUNA_ROOT%" -Character "%RIKO_CHARACTER%" -VoiceSource "%RIKO_VOICE_SOURCE%" -StartDiscord %DISCORD_ON% -AmyRoot "%HALMINI_ROOT%" -HalminiDb "%HALMINI_DB_ABS%" -DashboardPort %DASHBOARD_PORT% -ShareRoot "%HALMINI_SHARE%"
if errorlevel 1 (
    echo   Failed to start background services.
    pause
    exit /b 1
)

echo   Waiting for Vite ^(avatar^) + dashboard...
set "READY="
for /l %%I in (1,1,60) do (
    powershell -NoProfile -Command "try { (Invoke-WebRequest -UseBasicParsing http://127.0.0.1:5173 -TimeoutSec 1).StatusCode | Out-Null; (Invoke-WebRequest -UseBasicParsing '%DASHBOARD_URL%' -TimeoutSec 1).StatusCode | Out-Null; exit 0 } catch { exit 1 }" >nul 2>&1
    if not errorlevel 1 (
        set "READY=1"
        goto ready
    )
    if %%I==20 echo   still waiting...
    ping -n 2 127.0.0.1 >nul
)
:ready
if not defined READY (
    echo   Services did not come up. Check logs\server.err.log / dashboard.err.log / vite.err.log
    goto cleanup
)

for /l %%I in (1,1,20) do (
    powershell -NoProfile -Command "try { (Invoke-WebRequest -UseBasicParsing http://127.0.0.1:%SERVER_PORT%/health -TimeoutSec 1).StatusCode } catch { exit 1 }" >nul 2>&1
    if not errorlevel 1 goto api_ok
    ping -n 2 127.0.0.1 >nul
)
:api_ok

REM Electron avatar — visible, same as before (not the browser).
if not exist "desktop\node_modules\electron" (
    echo   Installing Electron desktop window ^(one time^)...
    pushd desktop
    call npm install
    popd
)
if exist "desktop\node_modules\electron" (
    echo   Desktop avatar: starting...
    powershell -NoProfile -Command "Start-Process -FilePath 'npx.cmd' -ArgumentList 'electron','.' -WorkingDirectory '%LUNA_ROOT%\desktop' -WindowStyle Hidden" >nul 2>&1
) else (
    echo   WARNING: Electron missing — avatar skipped. Vite is still on :5173
)

echo   Opening HalMini dashboard in browser...
start "" "%DASHBOARD_URL%"

echo.
echo   Avatar = Electron window. Dashboard = browser.
echo   Click the avatar once so audio can play.
if "%DISCORD_ON%"=="1" (
    echo   Discord: /luna join — answers only when someone says Luna.
)
echo   This launcher minimizes; close it to stop Luna.
echo.

powershell -NoProfile -Command "Add-Type -Name Z -Namespace W -MemberDefinition '[DllImport(\"user32.dll\")] public static extern bool ShowWindowAsync(IntPtr h,int n);'; Start-Sleep -Milliseconds 400; Get-Process | Where-Object { $_.MainWindowTitle -eq 'Luna HalMini' } | ForEach-Object { [W.Z]::ShowWindowAsync($_.MainWindowHandle, 6) | Out-Null }" >nul 2>&1

.venv\Scripts\python.exe -m server.chat_terminal

:cleanup
echo.
echo   Closing everything...
powershell -NoProfile -ExecutionPolicy Bypass -File "%LUNA_ROOT%\tools\stop_luna_procs.ps1" -LunaRoot "%LUNA_ROOT%" -AmyRoot "%HALMINI_ROOT%" -ShareRoot "%HALMINI_SHARE%" >nul 2>&1
taskkill /FI "WINDOWTITLE eq Luna Desktop*" /T /F >nul 2>&1
for /f "tokens=5" %%A in ('netstat -ano ^| findstr "LISTENING" ^| findstr ":%SERVER_PORT% "') do taskkill /PID %%A /T /F >nul 2>&1
for /f "tokens=5" %%A in ('netstat -ano ^| findstr "LISTENING" ^| findstr ":5173 "') do taskkill /PID %%A /T /F >nul 2>&1
for /f "tokens=5" %%A in ('netstat -ano ^| findstr "LISTENING" ^| findstr ":%DASHBOARD_PORT% "') do taskkill /PID %%A /T /F >nul 2>&1
echo   Done.
echo %CMDCMDLINE% | findstr /i /c:"/c" >nul
if errorlevel 1 pause

@echo off
REM Amy Smith is the main runtime (dashboard + Discord + memory + HalMini).
REM Persona is Luna. HalMini answers first; NVIDIA teacher on miss.
chcp 65001 >nul
setlocal
cd /d "%~dp0"
title Luna — Amy Smith stack

echo.
echo   HalMini-share
echo     Main:     amy-smith  ^(dashboard :3000, Discord, memory^)
echo     Persona:  Luna
echo     HalMini:  fact brain inside Amy Smith ^(Amy\ + Luna\data^)
echo     Teacher:  NVIDIA NIM on HalMini miss
echo.

if not exist "%~dp0amy-smith\start.bat" (
  echo   Missing amy-smith\start.bat
  pause
  exit /b 1
)

set "HALMINI_ENABLED=1"
set "HALMINI_SHARE=%~dp0"
if "%HALMINI_SHARE:~-1%"=="\" set "HALMINI_SHARE=%HALMINI_SHARE:~0,-1%"

cd /d "%~dp0amy-smith"
call start.bat

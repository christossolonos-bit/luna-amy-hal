@echo off
REM Amy Smith — dashboard, Discord, memory (Luna persona).
chcp 65001 >nul
setlocal
cd /d "%~dp0"
title Luna — Amy Smith

echo.
echo   Amy Smith
echo     Runtime:  amy-smith  (dashboard :3000, Discord, memory)
echo     Persona:  Luna
echo     Brain:    NVIDIA NIM
echo.

if not exist "%~dp0amy-smith\start.bat" (
  echo   Missing amy-smith\start.bat
  pause
  exit /b 1
)

cd /d "%~dp0amy-smith"
call start.bat

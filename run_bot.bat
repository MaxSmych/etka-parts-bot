@echo off
rem ============================================================
rem  etka-bot — автономный запуск с авто-рестартом при падении.
rem  Логи: data\bot.log (ротация при >5 МБ -> bot.log.old).
rem ============================================================
title etka-bot (autorun)
cd /d "%~dp0"
if not exist "data" mkdir "data"

set "UV=uv"
set "LOG=%~dp0data\bot.log"

:loop
rem --- ротация лога ---
for %%F in ("%LOG%") do if exist "%LOG%" if %%~zF GTR 5242880 move /y "%LOG%" "%LOG%.old" >nul 2>&1

echo [%date% %time%] Starting etka-bot...>> "%LOG%"
"%UV%" run etka-bot >> "%LOG%" 2>&1
echo [%date% %time%] Bot exited (code %ERRORLEVEL%). Restart in 10s...>> "%LOG%"
timeout /t 10 /nobreak >nul
goto loop

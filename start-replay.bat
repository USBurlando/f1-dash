@echo off
title F1-Dash Replay Launcher

echo F1-Dash Replay Stack
echo ====================
echo.

REM List available recordings
echo Available recordings:
echo.
set COUNT=0
for %%f in (recordings\*.ndjson) do (
    set /a COUNT+=1
    echo   [!COUNT!] %%f
)
setlocal enabledelayedexpansion
set COUNT=0
set LAST_FILE=
for %%f in (recordings\*.ndjson) do (
    set /a COUNT+=1
    set FILE_!COUNT!=%%f
    set LAST_FILE=%%f
)

echo.
if %COUNT%==0 (
    echo No recordings found in recordings\ folder.
    echo Run start-capture.bat during a live session first.
    pause
    exit /b
)

if %COUNT%==1 (
    set RECORDING=%LAST_FILE%
    echo Using: %LAST_FILE%
) else (
    set /p CHOICE="Select recording number (default=latest): "
    if "!CHOICE!"=="" set CHOICE=%COUNT%
    set RECORDING=!FILE_%CHOICE%!
)

echo.
echo Launching replay with: !RECORDING!
echo.

REM Terminal 1 — Simulator
start "F1 Simulator ^| port 4000" cmd /k "title F1 Simulator ^| port 4000 && set RUST_LOG=info && set ADDRESS=0.0.0.0:4000 && cargo run -p simulator -- replay !RECORDING!"

echo Waiting for simulator to initialize...
timeout /t 6 /nobreak > nul

REM Terminal 2 — Realtime
start "F1 Realtime ^| port 4001" cmd /k "title F1 Realtime ^| port 4001 && set RUST_LOG=info && set ADDRESS=0.0.0.0:4001 && set F1_DEV_URL=ws://localhost:4000/ws && set ORIGIN=http://localhost:3001 && cargo run -p realtime"

REM Terminal 3 — API
start "F1 API ^| port 4010" cmd /k "title F1 API ^| port 4010 && set RUST_LOG=info && set ADDRESS=0.0.0.0:4010 && set ORIGIN=http://localhost:3001 && cargo run -p api"

REM Terminal 4 — Dashboard
start "F1 Dashboard ^| port 3001" cmd /k "title F1 Dashboard ^| port 3001 && cd dashboard && set API_URL=http://localhost:4010 && set NEXT_PUBLIC_LIVE_URL=http://localhost:4001 && npm run dev"

echo.
echo All services starting...
echo.
echo Press any key to open http://localhost:3001 in your browser
echo (Wait ~15 seconds for first-time compilation)
pause > nul
start http://localhost:3001

@echo off
title F1-Dash Launcher

echo Starting F1-Dash LIVE Stack...
echo.

REM Terminal 1 — Realtime (connects directly to F1)
start "F1 Realtime Service (port 4001)" cmd /k "title F1 Realtime Service (port 4001) && set RUST_LOG=info && set ADDRESS=0.0.0.0:4001 && set ORIGIN=http://localhost:3001 && cargo run -p realtime"

REM Terminal 2 — API
start "F1 API Service (port 4010)" cmd /k "title F1 API Service (port 4010) && set RUST_LOG=info && set ADDRESS=0.0.0.0:4010 && set ORIGIN=http://localhost:3001 && cargo run -p api"

REM Terminal 3 — Dashboard
start "F1 Dashboard (port 3001)" cmd /k "title F1 Dashboard (port 3001) && cd dashboard && set API_URL=http://localhost:4010 && set NEXT_PUBLIC_LIVE_URL=http://localhost:4001 && npm run dev"

echo.
echo All services starting...
echo Dashboard will be available at http://localhost:3001
echo.
echo Press any key to open the browser (wait ~15 seconds for services to start)
pause > nul
start http://localhost:3001

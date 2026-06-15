@echo off
title F1 Data Capture

echo F1 Live Data Capture
echo ====================
echo.

REM Ask for output filename
set /p SESSION_NAME="Session name (e.g. canada_2026_fp1): "

if "%SESSION_NAME%"=="" (
    echo No name provided, using timestamp...
    set SESSION_NAME=session_%date:~-4%%date:~3,2%%date:~0,2%_%time:~0,2%%time:~3,2%
    set SESSION_NAME=%SESSION_NAME: =0%
)

set OUTPUT=recordings/%SESSION_NAME%.ndjson

echo.
echo Output file: %OUTPUT%
echo.
echo Starting capture... Press Ctrl+C to stop.
echo.

if not exist recordings mkdir recordings
python f1-capture-win.py %OUTPUT%

echo.
echo Capture complete!
pause

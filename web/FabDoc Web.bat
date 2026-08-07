@echo off
REM Double-click launcher for the web front end, matching the desktop one.
cd /d "%~dp0"

python -c "import fastapi, uvicorn, multipart, jinja2" 2>nul
if errorlevel 1 (
    echo Installing the web dependencies, one moment...
    python -m pip install -r requirements.txt
    if errorlevel 1 (
        echo.
        echo Could not install the dependencies. Check that Python 3.10+ is on PATH.
        pause
        exit /b 1
    )
)

REM Open the browser first: uvicorn holds the console until it is stopped.
start "" http://127.0.0.1:8000
echo.
echo FabDoc Organizer is running at http://127.0.0.1:8000
echo Close this window or press Ctrl+C to stop it.
echo.
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000

if errorlevel 1 (
    echo.
    echo The server stopped unexpectedly. Make sure port 8000 is free.
    pause
)

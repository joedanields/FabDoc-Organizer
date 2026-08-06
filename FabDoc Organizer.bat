@echo off
REM Double-click launcher for engineers who would rather not use a terminal.
cd /d "%~dp0"
python -m fabdoc
if errorlevel 1 (
    echo.
    echo FabDoc Organizer could not start.
    echo Make sure Python 3.10+ is installed, then run:  pip install -r requirements.txt
    pause
)

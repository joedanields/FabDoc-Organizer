@echo off
REM ---------------------------------------------------------------------------
REM  Build FabDoc Organizer (web) into a Windows application.
REM
REM  Double-click this, wait, and the finished app appears in  web\dist\.
REM
REM    Build FabDoc Web.bat            one folder, starts fast   (default)
REM    Build FabDoc Web.bat onefile    one single .exe, starts slower
REM
REM  The one-folder build is the default because it starts in about a second,
REM  where a single-file build has to unpack ~80 MB to a temp folder on every
REM  launch. Hand over the whole "FabDoc Organizer" folder and tell people to
REM  double-click the .exe inside it. Use onefile when it has to be one file.
REM ---------------------------------------------------------------------------
setlocal
cd /d "%~dp0"

set "APPNAME=FabDoc Organizer"
set "MODE=%~1"
if /i "%MODE%"=="onefile" (
    set "PACKAGING=--onefile"
    echo Building a single-file executable.
) else (
    set "PACKAGING=--onedir"
    echo Building a one-folder application. Pass "onefile" for a single .exe.
)

echo.
echo === 1/3  Checking Python =====================================
python --version >nul 2>&1
if errorlevel 1 (
    echo.
    echo Python was not found on PATH. Install Python 3.10 or later,
    echo ticking "Add python.exe to PATH", then run this again.
    pause
    exit /b 1
)
python --version

echo.
echo === 2/3  Installing build dependencies =======================
python -m pip install --disable-pip-version-check -q -r requirements.txt
if errorlevel 1 (
    echo.
    echo Could not install the app dependencies.
    pause
    exit /b 1
)
python -m pip install --disable-pip-version-check -q pyinstaller
if errorlevel 1 (
    echo.
    echo Could not install PyInstaller.
    pause
    exit /b 1
)

REM  PyInstaller follows imports through whatever is installed in this Python,
REM  not just what the app uses. On a machine that also does data work it walks
REM  pandas -> scipy -> tensorflow -> keras and bundles gigabytes of it. None of
REM  it is imported anywhere in fabdoc or web/, so it is excluded by name: the
REM  build is faster, the app is smaller, and if one of these were ever really
REM  needed the app would fail loudly on start rather than ship silently fat.
set "SKIP=--exclude-module tensorflow --exclude-module keras --exclude-module torch"
set "SKIP=%SKIP% --exclude-module pandas --exclude-module scipy --exclude-module numpy"
set "SKIP=%SKIP% --exclude-module matplotlib --exclude-module sklearn --exclude-module h5py"
set "SKIP=%SKIP% --exclude-module IPython --exclude-module notebook --exclude-module jedi"
set "SKIP=%SKIP% --exclude-module PIL --exclude-module cv2 --exclude-module sympy"
set "SKIP=%SKIP% --exclude-module pytest --exclude-module tkinter --exclude-module PySide6"
set "SKIP=%SKIP% --exclude-module PyQt5 --exclude-module PyQt6 --exclude-module wx"

echo.
echo === 3/3  Building - this takes a few minutes =================
REM  --add-data puts templates/ and static/ at the root of the bundle, which is
REM     where app.main looks for them when frozen (sys._MEIPASS).
REM  --paths .. lets PyInstaller resolve "import fabdoc"; main.py adds that
REM     directory at runtime, which a static analyser cannot see.
REM  --collect-all pulls in the parts uvicorn and PyMuPDF load by name at
REM     runtime. Without them the build succeeds and the app dies on start.
python -m PyInstaller ^
    --noconfirm --clean %PACKAGING% ^
    --name "%APPNAME%" ^
    --icon "static\img\favicon.ico" ^
    --paths ".." ^
    --add-data "templates;templates" ^
    --add-data "static;static" ^
    --collect-all uvicorn ^
    --collect-all fitz ^
    --collect-submodules multipart ^
    --hidden-import "app.main" ^
    --hidden-import "fastapi" ^
    --hidden-import "starlette" ^
    --hidden-import "openpyxl" ^
    --hidden-import "jinja2" ^
    %SKIP% ^
    "run_app.py"

if errorlevel 1 (
    echo.
    echo The build failed. The PyInstaller output above says why.
    pause
    exit /b 1
)

echo.
echo =============================================================
if /i "%MODE%"=="onefile" (
    echo  Done.  dist\%APPNAME%.exe
    echo.
    echo  Hand over that one file. It writes its trackers and
    echo  registers to a "data" folder beside itself.
) else (
    echo  Done.  dist\%APPNAME%\%APPNAME%.exe
    echo.
    echo  Hand over the whole "dist\%APPNAME%" folder. The .exe
    echo  needs the files beside it, and writes its trackers and
    echo  registers to a "data" folder in there.
)
echo =============================================================
echo.
pause
endlocal

@echo off
setlocal
cd /d "%~dp0"

echo ==========================================
echo SHP to DXF Converter - EXE Build
echo ==========================================
echo.

where python >nul 2>nul
if errorlevel 1 (
    echo [ERROR] Python was not found.
    echo Install Python 3.10 or newer and enable "Add Python to PATH".
    pause
    exit /b 1
)

echo [1/4] Checking Python...
python --version
if errorlevel 1 goto error

echo.
echo [2/4] Creating virtual environment...
if not exist ".venv\Scripts\python.exe" (
    python -m venv .venv
    if errorlevel 1 goto error
)

echo.
echo [3/4] Installing required packages...
".venv\Scripts\python.exe" -m pip install --upgrade pip
if errorlevel 1 goto error
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 goto error

echo.
echo [4/4] Building EXE...
".venv\Scripts\python.exe" -m PyInstaller --noconfirm --clean --onefile --windowed --name "SHP_DXF_Converter" shp_dxf_converter.py
if errorlevel 1 goto error

echo.
echo ==========================================
echo BUILD SUCCESS
echo EXE: %~dp0dist\SHP_DXF_Converter.exe
echo ==========================================
pause
exit /b 0

:error
echo.
echo ==========================================
echo BUILD FAILED
echo Review the error messages above.
echo Check Python installation and internet connection.
echo ==========================================
pause
exit /b 1

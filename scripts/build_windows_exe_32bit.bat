@echo off
setlocal

cd /d "%~dp0\.."

set "PYTHON32="
if exist "%LocalAppData%\Programs\Python\Python311-32\python.exe" (
  set "PYTHON32=%LocalAppData%\Programs\Python\Python311-32\python.exe"
)

if "%PYTHON32%"=="" (
  py -3.11-32 --version >nul 2>nul
  if errorlevel 1 (
    echo 32-bit Python 3.11 is required for Kawaii Securities and Kiwoom OpenAPI+.
    echo Install 32-bit Python, then rerun this script.
    exit /b 1
  )
  set "PYTHON32=py -3.11-32"
)

if not exist ".venv32" (
  %PYTHON32% -m venv .venv32
)

call ".venv32\Scripts\activate.bat"
python -c "import struct; raise SystemExit(0 if struct.calcsize('P')*8 == 32 else 1)"
if errorlevel 1 (
  echo This script must run with 32-bit Python.
  exit /b 1
)

python -m pip install --upgrade pip
python -m pip install -r requirements.txt
python -m pip install -r requirements-build.txt

python -m unittest discover -s tests
if errorlevel 1 exit /b 1

pyinstaller ^
  --noconfirm ^
  --clean ^
  --onefile ^
  --windowed ^
  --runtime-tmpdir "C:\Users\Public\Documents\ESTsoft\CreatorTemp" ^
  --additional-hooks-dir "scripts\pyinstaller_hooks" ^
  --runtime-hook "scripts\pyi_rth_tkinter_manual.py" ^
  --hidden-import _tkinter ^
  --hidden-import tkinter ^
  --hidden-import tkinter.ttk ^
  --icon "assets\kiwoom_trade.ico" ^
  --add-data "assets\kiwoom_trade.ico;assets" ^
  --add-data "assets\kiwoom_trade.png;assets" ^
  --name KawaiiSecurities-v67 ^
  kiwoom_auto_trader\main.py

if errorlevel 1 exit /b 1

echo.
echo Build complete: dist\KawaiiSecurities-v67.exe

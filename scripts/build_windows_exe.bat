@echo off
setlocal

cd /d "%~dp0\.."

set "PYTHON_CMD=py -3.11"
%PYTHON_CMD% --version >nul 2>nul
if errorlevel 1 (
  set "PYTHON_CMD=python"
)

if not exist ".venv" (
  %PYTHON_CMD% -m venv .venv
)

call ".venv\Scripts\activate.bat"
python -m pip install --upgrade pip
python -m pip install -r requirements-build.txt

python -m unittest discover -s tests
if errorlevel 1 exit /b 1

pyinstaller ^
  --noconfirm ^
  --clean ^
  --onefile ^
  --windowed ^
  --name KiwoomAutoTrader ^
  kiwoom_auto_trader\main.py

if errorlevel 1 exit /b 1

echo.
echo Build complete: dist\KiwoomAutoTrader.exe

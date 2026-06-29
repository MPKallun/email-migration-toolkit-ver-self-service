@echo off
cd /d "%~dp0"

:: Check if Python is installed
python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo Python is not installed. Please download it from https://www.python.org/downloads/
    pause
    exit /b 1
)

:: Set up virtual environment
if not exist "venv" (
    echo First-time setup: Creating virtual environment...
    python -m venv venv
)

:: Activate and install dependencies
call venv\Scripts\activate.bat
echo Updating packages...
python -m pip install --upgrade pip >nul 2>&1
pip install customtkinter

:: Run the app
echo Launching Data Backup app...
python src/gui.py

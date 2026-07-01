@echo off
REM Build the Windows app. Run: packaging\build_windows.bat
REM Supports signtool code-signing if WIN_SIGN_CERT_PATH and WIN_SIGN_PASSWORD are set.
cd /d "%~dp0\.."

py -m venv build-venv
call build-venv\Scripts\activate.bat
python -m pip install --upgrade pip
echo Installing dependencies...
pip install customtkinter pyinstaller

echo Building executable with PyInstaller...
pyinstaller --clean --noconfirm packaging\app.spec

set EXE_PATH=dist\Data Backup.exe

if not "%WIN_SIGN_CERT_PATH%"=="" (
    echo Signing application with certificate: %WIN_SIGN_CERT_PATH%...
    signtool sign /f "%WIN_SIGN_CERT_PATH%" /p "%WIN_SIGN_PASSWORD%" /tr http://timestamp.digicert.com /td sha256 /fd sha256 "%EXE_PATH%"
    echo Built and signed: %EXE_PATH%
) else (
    echo Built (unsigned): %EXE_PATH%
    echo If SmartScreen warns: More info -^> Run anyway.
)

pause

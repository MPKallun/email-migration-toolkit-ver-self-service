@echo off
REM Build the Windows app:  build_windows.bat  ->  dist\Data Backup.exe
cd /d "%~dp0"
py -m venv build-venv
call build-venv\Scripts\activate.bat
python -m pip install --upgrade pip
pip install customtkinter pyinstaller
pyinstaller --clean --noconfirm imap_backup.spec
echo.
echo Built: dist\Data Backup.exe
echo If Windows SmartScreen warns: click "More info" -^> "Run anyway" (it is unsigned).
echo To remove the warning, sign the .exe with a code-signing certificate.
pause

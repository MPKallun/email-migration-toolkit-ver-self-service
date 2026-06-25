# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec for the EAHI IMAP Backup app.
#   macOS   -> dist/IMAP Backup.app   (double-click bundle)
#   Windows -> dist/IMAP Backup.exe   (single file)
# Build with:  pyinstaller --clean --noconfirm imap_backup.spec
# (run the matching build_mac.sh / build_windows.bat — they set up the venv)
import os, sys
from PyInstaller.utils.hooks import collect_data_files, collect_submodules

# CustomTkinter ships theme/asset files that must travel with the app:
datas = collect_data_files('customtkinter')
hiddenimports = collect_submodules('customtkinter') + ['imap_backup', 'caldav_backup']

icon = None
if sys.platform == 'darwin' and os.path.exists('icon.icns'):
    icon = 'icon.icns'
elif sys.platform == 'win32' and os.path.exists('icon.ico'):
    icon = 'icon.ico'

a = Analysis(['imap_backup_gui.py'], pathex=['.'], binaries=[], datas=datas,
             hiddenimports=hiddenimports, hookspath=[], runtime_hooks=[],
             excludes=[], noarchive=False)
pyz = PYZ(a.pure)

if sys.platform == 'win32':
    # one-file .exe — everything bundled into a single distributable file
    exe = EXE(pyz, a.scripts, a.binaries, a.datas, [], name='IMAP Backup',
              console=False, icon=icon, upx=False)
else:
    # one-dir + .app bundle on macOS
    exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name='IMAP Backup',
              console=False, icon=icon, upx=False)
    coll = COLLECT(exe, a.binaries, a.datas, name='IMAP Backup', upx=False)
    if sys.platform == 'darwin':
        app = BUNDLE(coll, name='IMAP Backup.app',
                     bundle_identifier='com.eahi.imapbackup', icon=icon)

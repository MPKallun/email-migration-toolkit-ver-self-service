# -*- mode: python ; coding: utf-8 -*-
# Run from the REPO ROOT:  pyinstaller --clean --noconfirm packaging/app.spec
import os, sys
from PyInstaller.utils.hooks import collect_data_files, collect_submodules

# Calculate project root (assumes script is run from the repo root)
root = os.getcwd()

datas = collect_data_files('customtkinter')
hiddenimports = collect_submodules('customtkinter') + ['imap_backup', 'caldav_backup', 'gmail_upload', 'utils']

icon = None
if sys.platform == 'darwin' and os.path.exists(os.path.join(root, 'packaging/icon.icns')):
    icon = os.path.join(root, 'packaging/icon.icns')
elif sys.platform == 'win32' and os.path.exists(os.path.join(root, 'packaging/icon.ico')):
    icon = os.path.join(root, 'packaging/icon.ico')

a = Analysis([os.path.join(root, 'src/gui.py')], 
             pathex=[os.path.join(root, 'src')], 
             binaries=[], 
             datas=datas,
             hiddenimports=hiddenimports, 
             hookspath=[], 
             runtime_hooks=[],
             excludes=[], 
             noarchive=False)
pyz = PYZ(a.pure)

if sys.platform == 'win32':
    exe = EXE(pyz, a.scripts, a.binaries, a.datas, [], name='Data Backup',
              console=False, icon=icon, upx=False)
else:
    exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name='Data Backup',
              console=False, icon=icon, upx=False)
    coll = COLLECT(exe, a.binaries, a.datas, name='Data Backup', upx=False)
    if sys.platform == 'darwin':
        app = BUNDLE(coll, name='Data Backup.app',
                     bundle_identifier='com.eahi.databackup', icon=icon)

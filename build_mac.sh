#!/usr/bin/env bash
# Build the macOS app:  ./build_mac.sh   ->  dist/IMAP Backup.app
set -e
cd "$(dirname "$0")"
python3 -m venv build-venv
source build-venv/bin/activate
pip install --upgrade pip >/dev/null
pip install customtkinter pyinstaller
pyinstaller --clean --noconfirm imap_backup.spec
echo
echo "✓ Built:  dist/IMAP Backup.app"
echo "First launch on another Mac (unsigned app): right-click the app → Open → Open."
echo "To remove the warning for everyone, code-sign/notarize it with your Apple Developer ID."

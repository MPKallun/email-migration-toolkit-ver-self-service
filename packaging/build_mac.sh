#!/usr/bin/env bash
# Build the macOS app. Run from anywhere: bash packaging/build_mac.sh
# Supports Apple Developer signing if APPLE_SIGN_IDENTITY is set in environment or .env.
set -e

cd "$(dirname "$0")/.."            # repo root

# Load environment variables if .env exists
if [ -f ".env" ]; then
    export $(grep -v '^#' .env | xargs)
fi

python3 -m venv build-venv
source build-venv/bin/activate
pip install --upgrade pip >/dev/null
pip install customtkinter pyinstaller

pyinstaller --clean --noconfirm packaging/app.spec

APP_PATH="dist/Data Backup.app"

if [ -n "$APPLE_SIGN_IDENTITY" ]; then
    echo "Signing application with identity: $APPLE_SIGN_IDENTITY..."
    codesign --force --options runtime --sign "$APPLE_SIGN_IDENTITY" --deep "$APP_PATH"
    echo "✓ Signed successfully."
else
    echo "⚠ No APPLE_SIGN_IDENTITY found. Building unsigned (users will see Gatekeeper blocks)."
fi

echo
echo "✓ Built: $APP_PATH"
echo "Make a .dmg for release: bash packaging/make_dmg.sh"

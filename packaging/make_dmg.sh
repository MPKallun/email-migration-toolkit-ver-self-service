#!/usr/bin/env bash
# Wrap the built app into a .dmg and notarize it if credentials are set.
# Run AFTER build_mac.sh: bash packaging/make_dmg.sh
set -e
cd "$(dirname "$0")/.."            # repo root

# Load environment variables if .env exists
if [ -f ".env" ]; then
    export $(grep -v '^#' .env | xargs)
fi

APP="dist/Data Backup.app"
[ -d "$APP" ] || { echo "No \"$APP\" — run build_mac.sh first."; exit 1; }

OUT="dist/Data Backup.dmg"
rm -f "$OUT"

echo "Creating staging area..."
STAGE="$(mktemp -d)"
cp -R "$APP" "$STAGE/"
ln -s /Applications "$STAGE/Applications"

echo "Creating UDZO DMG..."
hdiutil create -volname "Data Backup" -srcfolder "$STAGE" -ov -format UDZO "$OUT"
rm -rf "$STAGE"

# 1. Sign the DMG
if [ -n "$APPLE_SIGN_IDENTITY" ]; then
    echo "Signing DMG with identity: $APPLE_SIGN_IDENTITY..."
    codesign --force --sign "$APPLE_SIGN_IDENTITY" "$OUT"
    echo "✓ DMG signed."
fi

# 2. Notarize the DMG
if [ -n "$APPLE_ID" ] && [ -n "$APPLE_PASSWORD" ] && [ -n "$APPLE_TEAM_ID" ]; then
    echo "Submitting DMG for Apple Notarization..."
    xcrun notarytool submit "$OUT" \
        --apple-id "$APPLE_ID" \
        --password "$APPLE_PASSWORD" \
        --team-id "$APPLE_TEAM_ID" \
        --wait
    
    echo "Stapling notarization ticket..."
    xcrun stapler staple "$OUT"
    echo "✓ Notarization and stapling complete."
else
    echo "⚠ Apple Notarization credentials not found (APPLE_ID, APPLE_PASSWORD, APPLE_TEAM_ID). Skipping."
fi

echo "✓ DMG Build Process Completed: $OUT"

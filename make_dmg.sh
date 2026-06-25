#!/usr/bin/env bash
# Wrap the built macOS app into a distributable .dmg.  Run AFTER ./build_mac.sh.
#   ./make_dmg.sh   ->  dist/Data Backup.dmg   (upload this to a GitHub Release)
set -e
cd "$(dirname "$0")"
APP="dist/Data Backup.app"
[ -d "$APP" ] || { echo "No \"$APP\" — run ./build_mac.sh first."; exit 1; }
OUT="dist/Data Backup.dmg"; rm -f "$OUT"
STAGE="$(mktemp -d)"
cp -R "$APP" "$STAGE/"
ln -s /Applications "$STAGE/Applications"          # drag-to-install target
hdiutil create -volname "Data Backup" -srcfolder "$STAGE" -ov -format UDZO "$OUT"
rm -rf "$STAGE"
echo "✓ Built: $OUT"
echo "Unsigned — first launch: recipients right-click the app → Open."

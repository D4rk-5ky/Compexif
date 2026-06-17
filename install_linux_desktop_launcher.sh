#!/usr/bin/env bash
set -euo pipefail

APP_NAME="Compexif"
EXE_PATH="$(pwd)/dist/Compexif"
ICON_SOURCE="$(pwd)/assets/Compexif_Exif_multi_size.png"
ICON_TARGET="$HOME/.local/share/icons/compexif.png"
DESKTOP_FILE="$HOME/.local/share/applications/compexif.desktop"

if [[ ! -x "$EXE_PATH" ]]; then
  echo "Could not find executable: $EXE_PATH"
  echo "Build first with: ./build_pyinstaller.sh"
  exit 1
fi

mkdir -p "$HOME/.local/share/icons" "$HOME/.local/share/applications"
cp "$ICON_SOURCE" "$ICON_TARGET"

cat > "$DESKTOP_FILE" <<DESKTOP
[Desktop Entry]
Type=Application
Name=$APP_NAME
Comment=Find similar images by embedded metadata dates
Exec=$EXE_PATH
Icon=$ICON_TARGET
Terminal=false
Categories=Graphics;Photography;Utility;
DESKTOP

chmod +x "$DESKTOP_FILE"

echo "Installed launcher: $DESKTOP_FILE"
echo "If your desktop does not refresh immediately, log out/in or run: update-desktop-database ~/.local/share/applications"

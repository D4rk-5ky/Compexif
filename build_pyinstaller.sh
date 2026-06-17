#!/usr/bin/env bash
set -euo pipefail

python3 -m pip install -r requirements-build.txt
python3 -m PyInstaller --noconfirm --clean Compexif.spec

echo "Build finished. One-file executable: dist/Compexif"
echo "Note: On Linux, the executable uses the icon as its window/app icon."
echo "For a file-manager launcher icon, run: ./install_linux_desktop_launcher.sh"

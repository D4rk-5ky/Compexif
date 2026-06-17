@echo off
py -m pip install -r requirements-build.txt
py -m PyInstaller --noconfirm --clean Compexif.spec
echo Build finished. One-file executable: dist\Compexif.exe
echo The .exe file should use the included Compexif icon as its file icon.
pause

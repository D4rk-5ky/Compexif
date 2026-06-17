# -*- mode: python ; coding: utf-8 -*-

# PyInstaller ONE-FILE build file for Compexif / Picture Metadata Compare.
# Build with:
#   python3 -m PyInstaller --noconfirm Compexif.spec
#
# Output:
#   Linux:   dist/Compexif
#   Windows: dist/Compexif.exe
#
# The icon option embeds the .ico as the executable file icon on Windows.
# On Linux, the same icon is used as the Qt window/app icon. Linux file-manager
# icons for executables are normally controlled by a .desktop launcher.

from PyInstaller.utils.hooks import collect_submodules

block_cipher = None


a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=[],
    datas=[
        ('image_compare_layout.ui', '.'),
        ('assets/Compexif_Exif_multi_size.ico', 'assets'),
    ],
    hiddenimports=collect_submodules('PIL'),
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)
pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name='Compexif',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon='assets/Compexif_Exif_multi_size.ico',
)

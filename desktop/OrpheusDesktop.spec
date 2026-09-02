# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec for the Windows build. Run from this directory:
#     pyinstaller OrpheusDesktop.spec
# → dist/OrpheusDesktop.exe  (one file, no console; logs go to
#   %LOCALAPPDATA%\Orpheus\orpheus-desktop.log)
import os
import sys

sys.path.insert(0, SPECPATH)
icon_path = None
try:
    from orpheus_desktop import make_icon_image
    os.makedirs(os.path.join(SPECPATH, "build"), exist_ok=True)
    icon_path = os.path.join(SPECPATH, "build", "orpheus.ico")
    make_icon_image("idle", 256).save(
        icon_path, sizes=[(16, 16), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
except Exception as e:  # Pillow missing: build without an icon
    print("exe icon skipped:", e)
    icon_path = None

a = Analysis(
    [os.path.join(SPECPATH, "orpheus_desktop.py")],
    pathex=[SPECPATH],
    binaries=[],
    datas=[],
    hiddenimports=[
        "pystray._win32",
        "PIL.Image", "PIL.ImageDraw",
        "pynput.keyboard._win32", "pynput.mouse._win32",
        "winsound", "winreg",
    ],
    hookspath=[],
    runtime_hooks=[],
    excludes=["tkinter", "matplotlib", "scipy"],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="OrpheusDesktop",
    debug=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    icon=icon_path,
)

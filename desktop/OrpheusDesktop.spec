# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec for the Windows build. Run from this directory:
#     pyinstaller OrpheusDesktop.spec
# → dist/OrpheusDesktop/  (a folder: OrpheusDesktop.exe + _internal/, no console;
#   logs go to %LOCALAPPDATA%\Orpheus\orpheus-desktop.log). installer/orpheus.iss
#   turns the folder into OrpheusSetup-<version>.exe.
#
# A folder, not one file: a one-file exe unpacks itself into %TEMP% and runs
# from there on every start, which is what malware droppers do, and antivirus
# heuristics treat it that way.
import os
import re
import sys

sys.path.insert(0, SPECPATH)
with open(os.path.join(SPECPATH, "orpheus_desktop.py"), encoding="utf-8") as f:
    VERSION = re.search(r'^VERSION = "([^"]+)"', f.read(), re.M).group(1)
os.makedirs(os.path.join(SPECPATH, "build"), exist_ok=True)

icon_path = None
try:
    from orpheus_desktop import make_icon_image
    icon_path = os.path.join(SPECPATH, "build", "orpheus.ico")
    make_icon_image("idle", 256).save(
        icon_path, sizes=[(16, 16), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
except Exception as e:  # Pillow missing: build without an icon
    print("exe icon skipped:", e)
    icon_path = None

# The exe's version resource. Product name and version are what code signing
# checks against the project, so they come from the VERSION the app reports.
VERSION_INFO = """VSVersionInfo(
  ffi=FixedFileInfo(filevers={nums}, prodvers={nums}, mask=0x3f, flags=0x0,
                    OS=0x40004, fileType=0x1, subtype=0x0, date=(0, 0)),
  kids=[
    StringFileInfo([StringTable('040904B0', [
      StringStruct('CompanyName', 'CocaKova'),
      StringStruct('FileDescription', 'Orpheus desktop dictation'),
      StringStruct('FileVersion', '{version}'),
      StringStruct('InternalName', 'OrpheusDesktop'),
      StringStruct('LegalCopyright', 'Copyright (c) 2026 CocaKova. MIT License.'),
      StringStruct('OriginalFilename', 'OrpheusDesktop.exe'),
      StringStruct('ProductName', 'Orpheus'),
      StringStruct('ProductVersion', '{version}')])]),
    VarFileInfo([VarStruct('Translation', [1033, 1200])])
  ]
)
"""
version_file = None
if sys.platform == "win32":
    nums = tuple(int(x) for x in (VERSION.split(".") + ["0"] * 4)[:4])
    version_file = os.path.join(SPECPATH, "build", "version_info.txt")
    with open(version_file, "w", encoding="utf-8") as f:
        f.write(VERSION_INFO.format(nums=nums, version=VERSION))

# The Settings window's look (customtkinter) ships fonts, theme JSON and an
# icon next to its Python code; collect them explicitly (pyinstaller-hooks-
# contrib has a hook for it too).
from PyInstaller.utils.hooks import collect_data_files
theme_datas = collect_data_files("customtkinter")

a = Analysis(
    [os.path.join(SPECPATH, "orpheus_desktop.py")],
    pathex=[SPECPATH],
    binaries=[],
    datas=theme_datas,
    hiddenimports=[
        "pystray._win32",
        "PIL.Image", "PIL.ImageDraw",
        "pynput.keyboard._win32", "pynput.mouse._win32",
        "winsound", "winreg",
        "tkinter", "tkinter.ttk",      # the Settings window
        "customtkinter",               # its look
        "PIL.ImageTk", "PIL._tkinter_finder",   # CTkImage draws through ImageTk
    ],
    hookspath=[],
    runtime_hooks=[],
    excludes=["matplotlib", "scipy"],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="OrpheusDesktop",
    debug=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    icon=icon_path,
    version=version_file,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="OrpheusDesktop",
)

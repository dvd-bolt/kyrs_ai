# -*- mode: python ; coding: utf-8 -*-

import os
import re
from pathlib import Path

from PyInstaller.utils.hooks import collect_submodules


SPEC_DIR = Path(SPECPATH).resolve()
PROJECT_ROOT = SPEC_DIR.parent
SOURCE_ROOT = PROJECT_ROOT / "src"
ENTRY_POINT = PROJECT_ROOT / "packaging" / "entrypoint.py"
RUNTIME_HOOK = PROJECT_ROOT / "packaging" / "runtime_worker_dispatch.py"
LIBREOFFICE_HELPER = (
    SOURCE_ROOT / "papercraft" / "infrastructure" / "render" / "libreoffice_update.py"
)
BUILD_VERSION = os.getenv("PAPERCRAFT_BUILD_VERSION", "0.2.0")
if re.fullmatch(r"\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.-]+)?", BUILD_VERSION) is None:
    raise ValueError("PAPERCRAFT_BUILD_VERSION must be a semantic version")
MAJOR, MINOR, PATCH = (int(part) for part in BUILD_VERSION.split("-", 1)[0].split("+", 1)[0].split("."))
VERSION_RESOURCE = PROJECT_ROOT / "build" / "pyinstaller-version.txt"
VERSION_RESOURCE.parent.mkdir(parents=True, exist_ok=True)
VERSION_RESOURCE.write_text(
    f"""VSVersionInfo(
  ffi=FixedFileInfo(
    filevers=({MAJOR}, {MINOR}, {PATCH}, 0),
    prodvers=({MAJOR}, {MINOR}, {PATCH}, 0),
    mask=0x3f,
    flags=0x0,
    OS=0x40004,
    fileType=0x1,
    subtype=0x0,
    date=(0, 0)
  ),
  kids=[
    StringFileInfo([
      StringTable(
        '040904B0',
        [
          StringStruct('CompanyName', 'PaperCraft AI Studio contributors'),
          StringStruct('FileDescription', 'PaperCraft AI Studio'),
          StringStruct('FileVersion', '{BUILD_VERSION}'),
          StringStruct('InternalName', 'PaperCraftAI'),
          StringStruct('LegalCopyright', 'Copyright PaperCraft AI Studio contributors'),
          StringStruct('OriginalFilename', 'PaperCraftAI.exe'),
          StringStruct('ProductName', 'PaperCraft AI Studio'),
          StringStruct('ProductVersion', '{BUILD_VERSION}')
        ]
      )
    ]),
    VarFileInfo([VarStruct('Translation', [1033, 1200])])
  ]
)""",
    encoding="utf-8",
)

hidden_imports = sorted(
    set(
        collect_submodules("papercraft")
        + collect_submodules("keyring.backends")
        + [
            "matplotlib.backends.backend_agg",
            "pythoncom",
            "win32com.client",
        ]
    )
)

a = Analysis(
    [str(ENTRY_POINT)],
    pathex=[str(SOURCE_ROOT)],
    binaries=[],
    datas=[
        (
            str(LIBREOFFICE_HELPER),
            "papercraft/infrastructure/render",
        )
    ],
    hiddenimports=hidden_imports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[str(RUNTIME_HOOK)],
    excludes=["PyQt6", "fastapi", "uvicorn"],
    noarchive=False,
    optimize=1,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="PaperCraftAI",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=True,
    hide_console="hide-early",
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    version=str(VERSION_RESOURCE),
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="PaperCraftAI",
)

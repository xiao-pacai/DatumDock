# -*- mode: python ; coding: utf-8 -*-
"""DatumDock 的 Windows PyInstaller 单目录构建定义。"""

from pathlib import Path

from PyInstaller.building.build_main import Analysis, COLLECT, EXE, PYZ


ROOT = Path(SPEC).resolve().parent
ASSETS = ROOT / "assets"

a = Analysis(
    [str(ROOT / "src" / "datumdock" / "__main__.py")],
    pathex=[str(ROOT / "src")],
    binaries=[],
    datas=[(str(ASSETS), "assets")],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="DatumDock",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    icon=str(ASSETS / "brand" / "datumdock-app-icon.ico"),
    version=str(ROOT / "installer" / "version_info.txt"),
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="DatumDock",
)

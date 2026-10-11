# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 配置：打包为 onedir 应用目录（主 exe + _internal 依赖），窗口程序无控制台。"""

import importlib.util
import os

ROOT = os.path.abspath(os.path.join(SPECPATH, ".."))

# pykakasi 自带 9MB 左右的词典数据（pykakasi/data/*.db），官方没提供 PyInstaller
# 钩子，必须显式收进来；没装 pykakasi 时保持空列表，不影响打包。
datas = []
hiddenimports = []
if importlib.util.find_spec("pykakasi"):
    from PyInstaller.utils.hooks import collect_data_files

    datas = collect_data_files("pykakasi")
    hiddenimports = ["pykakasi"]

a = Analysis(
    [os.path.join(ROOT, "lyrics_extractor", "__main__.py")],
    pathex=[ROOT],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
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
    name="LyricsExtractor",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="LyricsExtractor",
)

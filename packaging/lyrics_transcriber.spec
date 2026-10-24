# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 配置：打包为 onedir 应用目录（主程序 + _internal 依赖），窗口程序无控制台。

三平台通用（Windows/macOS/Linux 的 CI 与本地脚本都走这份 spec）：
图标按平台取用——Windows 用 app.ico，macOS 用构建时生成的 app.icns（没有则跳过），
Linux 不传图标（AppImage 由外层 desktop 文件指定图标）。
"""

import importlib.util
import os
import sys

ROOT = os.path.abspath(os.path.join(SPECPATH, ".."))

# pykakasi 自带 9MB 左右的词典数据（pykakasi/data/*.db），官方没提供 PyInstaller
# 钩子，必须显式收进来；没装 pykakasi 时保持空列表，不影响打包。
# 应用资源（含 Windows 图标）放进 _internal/lyrics_transcriber/assets，
# gui 里按 __file__ 同级的 assets 目录去找，源码运行和打包后都能命中。
assets_dir = os.path.join(ROOT, "lyrics_transcriber", "assets")

if sys.platform == "win32":
    icon_path = os.path.join(assets_dir, "app.ico")
elif sys.platform == "darwin":
    _icns = os.path.join(assets_dir, "app.icns")
    icon_path = _icns if os.path.exists(_icns) else None
else:
    icon_path = None

datas = [(assets_dir, "lyrics_transcriber/assets")]
hiddenimports = []
if importlib.util.find_spec("pykakasi"):
    from PyInstaller.utils.hooks import collect_data_files

    datas += collect_data_files("pykakasi")
    hiddenimports = ["pykakasi"]

# 增强包依赖绝不能进安装版（torch 一带就是 200MB+）。vocal_sep / whisper_local
# 对它们是函数内懒加载，运行时检测不到就按「未安装增强包」自动跳过——所以
# 打包时排除即可；但 PyInstaller 的静态分析会看见那些 import，只要这些库
# 恰好装在本机（比如开发环境装了 demucs），不排除就会被打进去（实测 23MB
# 的包涨到 254MB）。开发机装了增强包后重新打包，务必确认体积没暴涨。
excludes = ["torch", "demucs", "soundfile"]

a = Analysis(
    [os.path.join(ROOT, "lyrics_transcriber", "__main__.py")],
    pathex=[ROOT],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    excludes=excludes,
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="LyricsTranscriber",
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
    icon=icon_path,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="LyricsTranscriber",
)

if sys.platform == "darwin":
    # macOS 必须显式 BUNDLE 才会产出 dist/LyricsTranscriber.app；
    # 没有这段时只生成普通目录（不带 .app 外壳，无法拖进应用程序）
    app = BUNDLE(
        coll,
        exe,
        name="LyricsTranscriber.app",
        icon=icon_path,
        bundle_identifier="com.good.lyricstranscriber",
    )

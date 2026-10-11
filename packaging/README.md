# 打包说明

## 产物形态

和市面软件一样：`setup.exe` 安装向导 → 装进 `C:\Program Files\LyricsExtractor\`
（主 exe + `_internal` 依赖目录）→ 开始菜单/桌面快捷方式 → 控制面板可卸载。

## 一键构建

```bat
packaging\build_exe.bat
```

脚本做两件事：

1. **PyInstaller**（onedir 模式）把 Python 程序打包成应用目录 `dist\LyricsExtractor\`；
2. 找到 **Inno Setup 6** 后，用 `packaging\installer.iss` 把应用目录打成
   `dist\installer\歌词提取器-安装包-v0.1.0.exe`。

没装 Inno Setup 时第 2 步自动跳过，仍可直接运行 `dist\LyricsExtractor\LyricsExtractor.exe` 试用。

## 依赖

- Python + `requirements.txt`（含 pyinstaller）
- [Inno Setup 6](https://jrsoftware.org/isinfo.php)（只生成安装包时需要，装在任意位置均可，
  脚本会自动查找系统/用户安装目录）。
  简体中文安装界面所需的 `ChineseSimplified.isl` 已随仓库附带在 `packaging/` 下，
  不依赖 Inno 安装目录的语言文件。

## 手动分步执行

```bat
python -m PyInstaller --noconfirm --clean packaging\lyrics_extractor.spec --distpath dist --workpath build\pyinstaller
"C:\Program Files (x86)\Inno Setup 6\ISCC.exe" packaging\installer.iss
```

## 注意事项

- 发布新版本时，同步改 `installer.iss` 顶部的 `MyAppVersion` 和 `lyrics_extractor/__init__.py` 的 `__version__`。
- `AppId` 是应用的唯一标识，保持不变才能"覆盖安装/升级"，不要改动。
- 未做数字签名，首次运行/安装时 Windows SmartScreen 可能提示"未知发布者"，属正常现象；
  需要消除提示需购买代码签名证书（个人使用可忽略）。

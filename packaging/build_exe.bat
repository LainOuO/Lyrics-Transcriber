@echo off
rem 一键打包：PyInstaller 生成应用目录；若装有 Inno Setup 6，则继续生成 setup 安装包
setlocal
cd /d "%~dp0.."

echo [1/2] PyInstaller 打包应用目录...
python -m PyInstaller --noconfirm --clean packaging\lyrics_extractor.spec --distpath dist --workpath build\pyinstaller || goto :err

echo [2/2] 查找 Inno Setup 编译器...
set "ISCC=%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe"
if not exist "%ISCC%" set "ISCC=%ProgramFiles%\Inno Setup 6\ISCC.exe"
if not exist "%ISCC%" set "ISCC=%LocalAppData%\Programs\Inno Setup 6\ISCC.exe"
if not exist "%ISCC%" (
    echo 未找到 Inno Setup 6，已跳过安装包生成。
    echo 应用目录在 dist\LyricsExtractor\，可直接运行 LyricsExtractor.exe 试用；
    echo 安装 Inno Setup 6 后重新运行本脚本即可得到 setup 安装包。
    exit /b 0
)
"%ISCC%" packaging\installer.iss || goto :err
echo.
echo 完成！应用目录：dist\LyricsExtractor\
echo        安装包：dist\installer\歌词提取器-安装包-v0.2.0.exe
exit /b 0

:err
echo 打包失败
exit /b 1

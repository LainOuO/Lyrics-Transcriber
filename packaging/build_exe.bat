@echo off
rem 一键打包：PyInstaller 生成应用目录；若装有 Inno Setup 6，则继续生成 setup 安装包
setlocal
cd /d "%~dp0.."

rem 找一个装了 PyInstaller 的解释器：优先 Windows 启动器 py -3，其次 PATH 里的 python
set "PY=py -3"
%PY% -c "import PyInstaller" >nul 2>&1
if errorlevel 1 set "PY=python"
%PY% -c "import PyInstaller" >nul 2>&1
if errorlevel 1 (
    echo 未找到可用的 Python，或该环境未安装 PyInstaller。
    echo 请先运行：python -m pip install -r requirements.txt
    exit /b 1
)

echo [1/2] PyInstaller 打包应用目录（解释器：%PY%）...
%PY% -m PyInstaller --noconfirm --clean packaging\lyrics_extractor.spec --distpath dist --workpath build\pyinstaller || goto :err

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
echo        安装包目录：dist\installer\（歌词提取器-安装包-v*.exe，版本号取自 installer.iss）
exit /b 0

:err
echo 打包失败
exit /b 1

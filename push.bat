@echo off
rem 一键推送：自动提交当前所有改动并 push 到 GitHub（origin/main）
chcp 65001 >nul
setlocal enabledelayedexpansion
cd /d "%~dp0"

rem 确认当前目录是 Git 仓库
git rev-parse --is-inside-work-tree >nul 2>&1
if errorlevel 1 (
    echo [错误] 当前目录不是 Git 仓库：%CD%
    exit /b 1
)

echo === 当前改动 ===
git status --short

rem 检测是否存在未提交改动
set "HAS_CHANGE="
for /f "delims=" %%i in ('git status --porcelain') do set "HAS_CHANGE=1"

if defined HAS_CHANGE (
    set "MSG="
    set /p "MSG=请输入提交说明（直接回车则用默认）："
    if "!MSG!"=="" set "MSG=chore: 更新于 %DATE% %TIME%"

    echo.
    echo [1/3] 暂存全部改动...
    git add -A || goto :err

    echo [2/3] 提交：!MSG!
    git commit -m "!MSG!" || goto :err
) else (
    echo 工作区没有新改动，跳过提交。
)

echo [3/3] 推送到 origin/main...
git push origin main || goto :err

echo.
echo 完成！远程仓库：https://github.com/LainOuO/Lyrics-Transcriber
exit /b 0

:err
echo.
echo [失败] 操作中断，请查看上方报错信息。
exit /b 1

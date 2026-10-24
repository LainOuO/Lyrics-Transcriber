#!/bin/sh
# Linux 本地构建（在真机/WSL 上运行）：产出 LyricsTranscriber-vX-Linux.AppImage
# 依赖：python3 + python3-tk（apt 安装）、pip 依赖、wget。
set -e
cd "$(dirname "$0")/.."
VERSION=$(python3 -c "from lyrics_transcriber import __version__; print(__version__)")

python3 -m pip install -r requirements.txt
python3 -m PyInstaller --noconfirm --clean packaging/lyrics_transcriber.spec \
  --distpath dist --workpath build-pyinstaller

mkdir -p appdir/usr/bin
cp -r dist/LyricsTranscriber/* appdir/usr/bin/
cat > appdir/AppRun <<'APPRUN'
#!/bin/sh
HERE="$(dirname "$(readlink -f "$0")")"
exec "$HERE/usr/bin/LyricsTranscriber/LyricsTranscriber" "$@"
APPRUN
chmod +x appdir/AppRun
cp lyrics_transcriber/assets/app-1024.png appdir/lyricstranscriber.png
cat > appdir/lyricstranscriber.desktop <<DESKTOP
[Desktop Entry]
Type=Application
Name=LyricsTranscriber
Comment=歌词提取器（内嵌/在线/AI 转写，生成 .lrc）
Icon=lyricstranscriber
Exec=LyricsTranscriber
Categories=AudioVideo;Utility;
DESKTOP

wget -q https://github.com/AppImage/appimagetool/releases/download/continuous/appimagetool-x86_64.AppImage
chmod +x appimagetool-x86_64.AppImage
ARCH=x86_64 ./appimagetool-x86_64.AppImage --appimage-extract-and-run \
  appdir "LyricsTranscriber-v${VERSION}-Linux.AppImage"
echo "完成：LyricsTranscriber-v${VERSION}-Linux.AppImage（首次运行前 chmod +x）"

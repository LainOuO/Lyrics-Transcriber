#!/bin/sh
# macOS 本地构建（在真机上运行）：产出 dist/LyricsTranscriber-vX-macOS.dmg
# 依赖：python3（含 tkinter）、pip 依赖、Xcode 自带的 sips/iconutil/hdiutil。
set -e
cd "$(dirname "$0")/.."
VERSION=$(python3 -c "from lyrics_transcriber import __version__; print(__version__)")

# 图标：PNG → iconset → icns（PyInstaller 的 spec 会自动取用）
mkdir -p app.iconset
for s in 16 32 128 256 512; do
  sips -z $s $s lyrics_transcriber/assets/app-1024.png --out app.iconset/icon_${s}x${s}.png >/dev/null
  sips -z $((s*2)) $((s*2)) lyrics_transcriber/assets/app-1024.png --out app.iconset/icon_${s}x${s}@2x.png >/dev/null
done
iconutil -c icns app.iconset -o lyrics_transcriber/assets/app.icns

python3 -m pip install -r requirements.txt
python3 -m PyInstaller --noconfirm --clean packaging/lyrics_transcriber.spec \
  --distpath dist --workpath build-pyinstaller
hdiutil create -volname "LyricsTranscriber" \
  -srcfolder dist/LyricsTranscriber/LyricsTranscriber.app \
  -ov -format UDZO "dist/LyricsTranscriber-v${VERSION}-macOS.dmg"
echo "完成：dist/LyricsTranscriber-v${VERSION}-macOS.dmg"

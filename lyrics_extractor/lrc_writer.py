"""LRC 内容生成与 .lrc 文件写入。"""

from __future__ import annotations

from pathlib import Path

from lyrics_extractor import APP_NAME, __version__


def format_timestamp(ms: int) -> str:
    """毫秒 → LRC 时间戳 "[mm:ss.xx]"（分超过 99 时照常累加）。"""
    ms = max(0, int(ms))
    minutes, rem = divmod(ms, 60000)
    seconds, ms_rem = divmod(rem, 1000)
    return f"{minutes:02d}:{seconds:02d}.{ms_rem // 10:02d}"


def build_lrc(title: str, artist: str, album: str, lines: list, synced: bool) -> str:
    """生成 LRC 文本。synced 时 lines 为 [(毫秒, 文本), ...]，否则为 [文本, ...]。"""
    header = [f"[ti:{title}]", f"[ar:{artist}]"]
    if album:
        header.append(f"[al:{album}]")
    header.append(f"[by:{APP_NAME} v{__version__}]")

    body: list[str] = []
    if synced:
        seen = set()
        ordered = sorted(lines, key=lambda x: x[0])
        for ms, text in ordered:
            key = (ms, text)
            if key in seen:
                continue
            seen.add(key)
            stamp = format_timestamp(ms)
            body.append(f"[{stamp}]{text}" if text else f"[{stamp}]")
    else:
        body = [text for text in (str(line).strip() for line in lines) if text]

    return "\n".join(header + body) + "\n"


def lrc_path_for(mp3_path: str | Path) -> Path:
    return Path(mp3_path).with_suffix(".lrc")


def write_lrc(
    mp3_path: str | Path,
    title: str,
    artist: str,
    album: str,
    lines: list,
    synced: bool,
) -> Path:
    """在 mp3 同目录写入同名 .lrc（UTF-8），返回写入路径。"""
    path = lrc_path_for(mp3_path)
    path.write_text(
        build_lrc(title, artist, album, lines, synced), encoding="utf-8"
    )
    return path

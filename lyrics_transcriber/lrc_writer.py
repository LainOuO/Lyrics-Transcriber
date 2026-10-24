"""LRC 内容生成、解析与 .lrc 文件写入。"""

from __future__ import annotations

import re
from pathlib import Path

from lyrics_transcriber import APP_NAME, __version__

# 行首 LRC 时间戳 [mm:ss]、[mm:ss.xx]、[mm:ss.xxx]，允许一行多个
_LRC_STAMP = re.compile(r"\[(\d{1,3}):(\d{1,2})(?:[.:](\d{1,3}))?\]")

# 增强型 LRC 的逐字时间戳 <mm:ss.xx>：只出现在正文中间，解析行时间轴后应剥掉
_WORD_STAMP = re.compile(r"<\d{1,3}:\d{1,2}(?:[.:]\d{1,3})?>")


def parse_lrc_text(text: str) -> list | None:
    """若文本中过半的非空行带时间戳（且至少两行），解析为 (毫秒, 文本) 列表；否则 None。

    lrclib 的同步歌词、以及藏在 USLT 里的整段 LRC 都靠它还原成时间轴。
    """
    non_empty = [line.strip() for line in text.splitlines() if line.strip()]
    if not non_empty:
        return None
    stamped = 0
    lines: list[tuple[int, str]] = []
    for line in non_empty:
        stamps = []
        pos = 0
        while (m := _LRC_STAMP.match(line, pos)) is not None:
            stamps.append(m)
            pos = m.end()
        if not stamps:
            continue
        stamped += 1
        body = _WORD_STAMP.sub("", line[pos:]).strip()
        for m in stamps:
            frac = m.group(3) or ""
            frac_ms = int(frac) * 10 ** (3 - len(frac)) if frac else 0
            ms = int(m.group(1)) * 60000 + int(m.group(2)) * 1000 + frac_ms
            lines.append((ms, body))
    if stamped >= 2 and stamped * 2 >= len(non_empty):
        lines.sort(key=lambda x: x[0])
        return lines
    return None


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

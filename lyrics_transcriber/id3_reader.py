"""用 mutagen 读取 ID3 标签与内嵌歌词（SYLT 同步歌词 / USLT 非同步歌词）。

mp3 直接使用本模块；wav / aiff 等容器内嵌的 ID3 也复用同一套逻辑
（见 :mod:`lyrics_transcriber.audio_file`）。
"""

from __future__ import annotations

from dataclasses import dataclass

from lyrics_transcriber.lrc_writer import parse_lrc_text
from mutagen.mp3 import MP3


@dataclass
class TrackTags:
    title: str = ""
    artist: str = ""
    album: str = ""
    duration_seconds: float = 0.0


@dataclass
class EmbeddedLyrics:
    source: str  # "SYLT" 或 "USLT"
    synced: bool
    # synced 时为 [(毫秒, 文本), ...]，plain 时为 [文本, ...]
    lines: list


def load_mp3(path) -> MP3:
    return MP3(str(path))


def _first_text(mp3_tags, frame_id: str) -> str:
    frames = mp3_tags.getall(frame_id)
    if not frames or not frames[0].text:
        return ""
    return str(frames[0].text[0])


def id3_title_artist_album(id3_tags) -> tuple[str, str, str]:
    """从 ID3 标签对象读取 (标题, 歌手, 专辑)，缺失时为空串。

    供 mp3 及内嵌 ID3 的容器（wav / aiff）复用。
    """
    if id3_tags is None:
        return ("", "", "")
    return (
        _first_text(id3_tags, "TIT2"),
        _first_text(id3_tags, "TPE1"),
        _first_text(id3_tags, "TALB"),
    )


def read_tags(mp3: MP3) -> TrackTags:
    tags = TrackTags()
    tags.title, tags.artist, tags.album = id3_title_artist_album(mp3.tags)
    tags.duration_seconds = float(mp3.info.length) if mp3.info else 0.0
    return tags


def read_embedded_lyrics(mp3_tags) -> EmbeddedLyrics | None:
    """按 SYLT > USLT 的优先级取内嵌歌词，都没有时返回 None。"""
    if mp3_tags is None:
        return None
    for frame in mp3_tags.getall("SYLT"):
        parsed = _parse_sylt(frame)
        if parsed is not None:
            return parsed
    for frame in mp3_tags.getall("USLT"):
        parsed = _parse_uslt(frame)
        if parsed is not None:
            return parsed
    return None


def _parse_sylt(frame) -> EmbeddedLyrics | None:
    # format=2 表示时间戳单位为毫秒；format=1（MPEG 帧数）无法可靠换算，只能放弃
    if frame.format != 2:
        return None
    lines: list[tuple[int, str]] = []
    for item in frame.text:
        if not isinstance(item, (tuple, list)) or len(item) != 2:
            continue
        text, ms = (item[0], item[1]) if isinstance(item[1], int) else (item[1], item[0])
        if not isinstance(ms, int):
            continue
        # 一条同步文本内可能带有换行，拆成共享同一时间戳的多行
        for sub in str(text).splitlines():
            sub = sub.strip()
            if sub:
                lines.append((int(ms), sub))
    if not lines:
        return None
    lines.sort(key=lambda x: x[0])
    return EmbeddedLyrics(source="SYLT", synced=True, lines=lines)


def _parse_uslt(frame) -> EmbeddedLyrics | None:
    text = frame.text or ""
    if not text.strip():
        return None
    parsed = parse_lrc_text(text)
    if parsed is not None:
        return EmbeddedLyrics(source="USLT", synced=True, lines=parsed)
    plain = [line.strip() for line in text.splitlines() if line.strip()]
    return EmbeddedLyrics(source="USLT", synced=False, lines=plain)

"""统一音频标签与内嵌歌词读取层。

用 mutagen 统一读取主流音频容器的标签与内嵌歌词，屏蔽各格式差异：

- mp3 / wav / aiff：容器内嵌 ID3，复用 :mod:`lyrics_extractor.id3_reader` 的
  SYLT（同步歌词）> USLT（纯文本）逻辑；
- flac / ogg / opus：VorbisComment，取 ``LYRICS`` / ``UNSYNCEDLYRICS`` 等字段；
- m4a / mp4：``\\xa9lyr``（无同步歌词标准，纯文本）；
- wma / asf：``WM/Lyrics``；
- ape：APEv2 的 ``Lyrics`` 字段。

统一输出 :class:`~lyrics_extractor.id3_reader.TrackTags` 与
:class:`~lyrics_extractor.id3_reader.EmbeddedLyrics`，时长统一取 ``info.length``。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import mutagen
from mutagen._vorbis import VCommentDict
from mutagen.apev2 import APEv2
from mutagen.asf import ASFTags
from mutagen.id3 import ID3
from mutagen.mp4 import MP4Tags

from lyrics_extractor import id3_reader
from lyrics_extractor.id3_reader import EmbeddedLyrics, TrackTags
from lyrics_extractor.lrc_writer import parse_lrc_text

# 支持扫描与读取的音频扩展名（小写，含点）
SUPPORTED_EXTENSIONS = frozenset(
    {
        ".mp3",
        ".flac",
        ".wav",
        ".m4a",
        ".mp4",
        ".aac",
        ".ogg",
        ".opus",
        ".wma",
        ".ape",
        ".aiff",
        ".aif",
    }
)

# 供界面/文档展示的格式清单
SUPPORTED_EXTENSIONS_TEXT = (
    "mp3 / flac / wav / m4a / mp4 / aac / ogg / opus / wma / ape / aiff"
)

# 裸流容器：ADTS 的 .aac 本身没有标签标准，解析失败也按"无标签"处理，不判为损坏
_TAGLESS_BY_DESIGN = frozenset({".aac"})

# VorbisComment 里可能承载歌词的字段，按优先级排列
_VORBIS_LYRIC_KEYS = ("SYNCEDLYRICS", "LYRICS", "UNSYNCEDLYRICS", "UNSYNCED LYRICS")


class AudioError(Exception):
    """文件无法解析（格式不支持、文件损坏等）。"""


@dataclass
class AudioFile:
    """一次加载的结果：统一标签、内嵌歌词与原始 mutagen 对象。"""

    path: Path
    tags: TrackTags
    embedded: EmbeddedLyrics | None
    raw: object | None = None


def _open(path):
    """包一层便于测试替换。"""
    return mutagen.File(str(path))


def load_audio(path) -> AudioFile:
    """加载音频并读取统一标签与内嵌歌词；无法解析时抛 :class:`AudioError`。"""
    path = Path(path)
    tagless_ok = path.suffix.lower() in _TAGLESS_BY_DESIGN
    try:
        raw = _open(path)
    except Exception as exc:
        if tagless_ok:
            return AudioFile(path=path, tags=TrackTags(), embedded=None)
        raise AudioError(f"无法解析音频: {exc}") from exc

    if raw is None:
        if tagless_ok:
            return AudioFile(path=path, tags=TrackTags(), embedded=None)
        raise AudioError(f"无法解析音频: 不是可识别的音频文件或已损坏（{path.name}）")

    info = getattr(raw, "info", None)
    duration = float(info.length) if info is not None else 0.0
    tags_obj = getattr(raw, "tags", None)
    return AudioFile(
        path=path,
        tags=read_tags(tags_obj, duration),
        embedded=read_embedded_lyrics(tags_obj),
        raw=raw,
    )


# ----------------------------------------------------------------------
# 标签字段读取


def _first(value) -> str:
    """把标签值统一成字符串：列表取首项，其余 ``str()`` 后去空白。"""
    if value is None:
        return ""
    if isinstance(value, (list, tuple)):
        if not value:
            return ""
        value = value[0]
    return str(value).strip()


def _lookup(tags, keys) -> str:
    for key in keys:
        try:
            text = _first(tags.get(key))
        except Exception:  # 标签对象千奇百怪，读不到就当空
            text = ""
        if text:
            return text
    return ""


def read_tags(tags_obj, duration: float = 0.0) -> TrackTags:
    """按标签对象类型读取统一的标题/歌手/专辑/时长。"""
    tags = TrackTags(duration_seconds=float(duration or 0.0))
    if tags_obj is None:
        return tags

    if isinstance(tags_obj, ID3):
        tags.title, tags.artist, tags.album = id3_reader.id3_title_artist_album(tags_obj)
    elif isinstance(tags_obj, VCommentDict):
        tags.title = _lookup(tags_obj, ("title",))
        tags.artist = _lookup(tags_obj, ("artist",))
        tags.album = _lookup(tags_obj, ("album",))
    elif isinstance(tags_obj, MP4Tags):
        tags.title = _lookup(tags_obj, ("\xa9nam",))
        # 不兜底 aART——那是专辑歌手（合集里往往不是唱这首歌的人）
        tags.artist = _lookup(tags_obj, ("\xa9ART",))
        tags.album = _lookup(tags_obj, ("\xa9alb",))
    elif isinstance(tags_obj, ASFTags):
        tags.title = _lookup(tags_obj, ("Title",))
        # 不兜底 WM/AlbumArtist——专辑歌手≠歌手，喂给在线搜索会找错人
        tags.artist = _lookup(tags_obj, ("Author",))
        tags.album = _lookup(tags_obj, ("WM/AlbumTitle",))
    elif isinstance(tags_obj, APEv2):
        tags.title = _lookup(tags_obj, ("Title",))
        tags.artist = _lookup(tags_obj, ("Artist",))
        tags.album = _lookup(tags_obj, ("Album",))
    return tags


# ----------------------------------------------------------------------
# 内嵌歌词读取


def read_embedded_lyrics(tags_obj) -> EmbeddedLyrics | None:
    """按容器类型读取内嵌歌词，都没有时返回 None。"""
    if tags_obj is None:
        return None

    if isinstance(tags_obj, ID3):
        return id3_reader.read_embedded_lyrics(tags_obj)
    if isinstance(tags_obj, VCommentDict):
        for key in _VORBIS_LYRIC_KEYS:
            text = _first(tags_obj.get(key))
            if text:
                return _to_embedded(key, text)
        return None
    if isinstance(tags_obj, MP4Tags):
        return _to_embedded("MP4", _first(tags_obj.get("\xa9lyr")))
    if isinstance(tags_obj, ASFTags):
        return _to_embedded("ASF", _first(tags_obj.get("WM/Lyrics")))
    if isinstance(tags_obj, APEv2):
        return _to_embedded("APE", _first(tags_obj.get("Lyrics")))
    return None


def _to_embedded(source: str, text: str) -> EmbeddedLyrics | None:
    """整段文本若本身是 LRC 就还原时间轴，否则按纯文本行返回。"""
    if not text or not text.strip():
        return None
    parsed = parse_lrc_text(text)
    if parsed:
        return EmbeddedLyrics(source=source, synced=True, lines=parsed)
    plain = [line.strip() for line in text.splitlines() if line.strip()]
    if not plain:
        return None
    return EmbeddedLyrics(source=source, synced=False, lines=plain)

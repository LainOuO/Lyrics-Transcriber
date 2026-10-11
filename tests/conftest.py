"""测试公共夹具。

- 用最小合法字节构造各格式音频，供读取层与全链路测试使用；
- mp3 用最小 MPEG 帧；flac / wav / aiff 可写入真实标签；
- mp4 / ogg / opus / wma / ape 构造完整文件过于复杂，改为直接构造真实的
  mutagen 标签对象，配合 ``patch_audio`` 替换底层打开动作来测读取层。
"""

import struct
import wave
from pathlib import Path
from types import SimpleNamespace

import pytest
from mutagen.aiff import AIFF
from mutagen.flac import FLAC
from mutagen.id3 import ID3, SYLT, TALB, TIT2, TPE1, USLT
from mutagen.wave import WAVE

from lyrics_extractor import audio_file

# MPEG1 Layer3, 128kbps, 44100Hz：帧长 = 144 * 128000 / 44100 = 417 字节
MPEG_FRAME = b"\xff\xfb\x90\x00" + b"\x00" * 413

# ADTS（AAC-LC, 44100Hz, 2ch）头；裸流本身没有标签区
ADTS_HEADER = bytes([0xFF, 0xF1, 0x50, 0x80, 0x01, 0x7F, 0xFC])


# ----------------------------------------------------------------------
# 最小合法音频文件字节构造


def make_mp3_bytes(n: int = 20) -> bytes:
    return MPEG_FRAME * n


def write_wav(path, *, sample_rate=8000, channels=1, bits=8, n_samples=8000):
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(channels)
        handle.setsampwidth(bits // 8)
        handle.setframerate(sample_rate)
        handle.writeframes(b"\x80" * n_samples)
    return path


def write_flac(path, *, sample_rate=44100, channels=2, bits=16, total_samples=44100):
    """``fLaC`` + 单个 STREAMINFO 元数据块，足以被 mutagen 解析。"""
    streaminfo = bytearray(34)
    struct.pack_into(">H", streaminfo, 0, 4096)  # min block size
    struct.pack_into(">H", streaminfo, 2, 4096)  # max block size
    packed = (
        (sample_rate << 44)
        | ((channels - 1) << 41)
        | ((bits - 1) << 36)
        | total_samples
    )
    struct.pack_into(">Q", streaminfo, 10, packed)
    path.write_bytes(b"fLaC" + bytes([0x80]) + b"\x00\x00\x22" + bytes(streaminfo))
    return path


def _extended80(value: float) -> bytes:
    """AIFF 的 80 位扩展浮点采样率表示。"""
    if value == 0:
        return b"\x00" * 10
    exponent = 0
    while value >= 2:
        value /= 2
        exponent += 1
    while value < 1:
        value *= 2
        exponent -= 1
    return struct.pack(">HQ", 16383 + exponent, int(value * (1 << 63)))


def write_aiff(path, *, sample_rate=8000, channels=1, bits=8, n_samples=8000):
    data = b"\x00" * n_samples
    comm = struct.pack(">hIh", channels, n_samples, bits) + _extended80(float(sample_rate))
    chunks = b"COMM" + struct.pack(">I", len(comm)) + comm
    chunks += b"SSND" + struct.pack(">I", 8 + len(data)) + struct.pack(">II", 0, 0) + data
    path.write_bytes(b"FORM" + struct.pack(">I", 4 + len(chunks)) + b"AIFF" + chunks)
    return path


def write_aac(path, *, frames=10):
    path.write_bytes(ADTS_HEADER * frames)
    return path


def add_id3_frames(
    tags, *, title=None, artist=None, album=None, uslt=None, sylt=None, sylt_format=2
):
    """往 ID3 标签对象里塞标题/歌手/专辑与歌词帧（mp3 / wav / aiff 共用）。"""
    if title:
        tags.add(TIT2(encoding=3, text=title))
    if artist:
        tags.add(TPE1(encoding=3, text=artist))
    if album:
        tags.add(TALB(encoding=3, text=album))
    if uslt is not None:
        tags.add(USLT(encoding=3, lang="chi", desc="lyrics", text=uslt))
    if sylt is not None:
        tags.add(
            SYLT(
                encoding=3,
                lang="chi",
                format=sylt_format,
                type=1,
                desc="synced",
                text=[(text, ms) for text, ms in sylt],
            )
        )
    return tags


# ----------------------------------------------------------------------
# 文件工厂夹具


@pytest.fixture
def mp3_factory(tmp_path):
    """在临时目录里生成测试 mp3，可按需携带标签；返回文件路径。"""
    counter = {"i": 0}

    def _make(
        name: str = "song.mp3",
        *,
        dirpath=None,
        title: str | None = None,
        artist: str | None = None,
        album: str | None = None,
        uslt: str | None = None,
        sylt: list | None = None,  # [(text, ms), ...]
        sylt_format: int = 2,
        raw: bytes | None = None,
    ):
        counter["i"] += 1
        base = Path(dirpath) if dirpath is not None else tmp_path
        path = base / f"{counter['i']:03d}-{name}"
        path.write_bytes(raw if raw is not None else make_mp3_bytes())
        if title or artist or album or uslt is not None or sylt is not None:
            tags = ID3()
            add_id3_frames(
                tags,
                title=title,
                artist=artist,
                album=album,
                uslt=uslt,
                sylt=sylt,
                sylt_format=sylt_format,
            )
            tags.save(str(path), v2_version=3)
        return path

    return _make


@pytest.fixture
def flac_factory(tmp_path):
    """生成可写标签的最小 FLAC（VorbisComment）。"""
    counter = {"i": 0}

    def _make(
        name: str = "song.flac",
        *,
        dirpath=None,
        title: str | None = None,
        artist: str | None = None,
        album: str | None = None,
        lyrics: str | None = None,
        unsynced: str | None = None,
        raw: bytes | None = None,
    ):
        counter["i"] += 1
        base = Path(dirpath) if dirpath is not None else tmp_path
        path = base / f"{counter['i']:03d}-{name}"
        if raw is not None:
            path.write_bytes(raw)
        else:
            write_flac(path)
        if title or artist or album or lyrics is not None or unsynced is not None:
            audio = FLAC(str(path))
            if title:
                audio["TITLE"] = title
            if artist:
                audio["ARTIST"] = artist
            if album:
                audio["ALBUM"] = album
            if lyrics is not None:
                audio["LYRICS"] = lyrics
            if unsynced is not None:
                audio["UNSYNCEDLYRICS"] = unsynced
            audio.save()
        return path

    return _make


@pytest.fixture
def wav_factory(tmp_path):
    """生成可写内嵌 ID3 的最小 WAV。"""
    counter = {"i": 0}

    def _make(
        name: str = "song.wav",
        *,
        dirpath=None,
        title: str | None = None,
        artist: str | None = None,
        album: str | None = None,
        uslt: str | None = None,
        sylt: list | None = None,
        raw: bytes | None = None,
    ):
        counter["i"] += 1
        base = Path(dirpath) if dirpath is not None else tmp_path
        path = base / f"{counter['i']:03d}-{name}"
        if raw is not None:
            path.write_bytes(raw)
        else:
            write_wav(path)
        if title or artist or album or uslt is not None or sylt is not None:
            audio = WAVE(str(path))
            audio.add_tags()
            add_id3_frames(
                audio.tags, title=title, artist=artist, album=album, uslt=uslt, sylt=sylt
            )
            audio.save()
        return path

    return _make


@pytest.fixture
def aiff_factory(tmp_path):
    """生成可写内嵌 ID3 的最小 AIFF。"""
    counter = {"i": 0}

    def _make(
        name: str = "song.aiff",
        *,
        dirpath=None,
        title: str | None = None,
        artist: str | None = None,
        album: str | None = None,
        uslt: str | None = None,
        sylt: list | None = None,
        raw: bytes | None = None,
    ):
        counter["i"] += 1
        base = Path(dirpath) if dirpath is not None else tmp_path
        path = base / f"{counter['i']:03d}-{name}"
        if raw is not None:
            path.write_bytes(raw)
        else:
            write_aiff(path)
        if title or artist or album or uslt is not None or sylt is not None:
            audio = AIFF(str(path))
            audio.add_tags()
            add_id3_frames(
                audio.tags, title=title, artist=artist, album=album, uslt=uslt, sylt=sylt
            )
            audio.save()
        return path

    return _make


@pytest.fixture
def aac_factory(tmp_path):
    """生成裸流 ADTS 的 .aac（无标签区）。"""
    counter = {"i": 0}

    def _make(name: str = "song.aac", *, dirpath=None, frames: int = 10, raw=None):
        counter["i"] += 1
        base = Path(dirpath) if dirpath is not None else tmp_path
        path = base / f"{counter['i']:03d}-{name}"
        if raw is not None:
            path.write_bytes(raw)
        else:
            write_aac(path, frames=frames)
        return path

    return _make


# ----------------------------------------------------------------------
# 各容器标签对象构造（配合 patch_audio 测读取层）


@pytest.fixture
def vorbis_tags():
    """FLAC / OGG / Opus 共用的 VorbisComment 标签对象。"""

    def _make(**fields):
        from mutagen._vorbis import VCommentDict

        tags = VCommentDict()
        for key, value in fields.items():
            if value is not None:
                tags[key] = [value]
        return tags

    return _make


@pytest.fixture
def mp4_tags():
    def _make(*, title=None, artist=None, album=None, lyrics=None):
        from mutagen.mp4 import MP4Tags

        tags = MP4Tags()
        if title:
            tags["\xa9nam"] = [title]
        if artist:
            tags["\xa9ART"] = [artist]
        if album:
            tags["\xa9alb"] = [album]
        if lyrics is not None:
            tags["\xa9lyr"] = [lyrics]
        return tags

    return _make


@pytest.fixture
def asf_tags():
    def _make(*, title=None, artist=None, album=None, lyrics=None):
        from mutagen.asf import ASFTags, ASFUnicodeAttribute

        tags = ASFTags()
        if title:
            tags["Title"] = [ASFUnicodeAttribute(title)]
        if artist:
            tags["Author"] = [ASFUnicodeAttribute(artist)]
        if album:
            tags["WM/AlbumTitle"] = [ASFUnicodeAttribute(album)]
        if lyrics is not None:
            tags["WM/Lyrics"] = [ASFUnicodeAttribute(lyrics)]
        return tags

    return _make


@pytest.fixture
def ape_tags():
    def _make(*, title=None, artist=None, album=None, lyrics=None):
        from mutagen.apev2 import APEv2, APEValue, TEXT

        tags = APEv2()
        if title:
            tags["Title"] = APEValue(title, TEXT)
        if artist:
            tags["Artist"] = APEValue(artist, TEXT)
        if album:
            tags["Album"] = APEValue(album, TEXT)
        if lyrics is not None:
            tags["Lyrics"] = APEValue(lyrics, TEXT)
        return tags

    return _make


@pytest.fixture
def patch_audio(monkeypatch):
    """让下一次 load_audio 读到指定的标签对象，绕过难以构造的真实容器。"""

    def _patch(tags, length: float = 180.0):
        raw = SimpleNamespace(tags=tags, info=SimpleNamespace(length=length))
        monkeypatch.setattr(audio_file, "_open", lambda path: raw)
        return raw

    return _patch

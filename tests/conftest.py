"""测试公共夹具：用最小合法 MPEG 帧构造可被 mutagen 解析的 mp3。"""

import pytest
from mutagen.id3 import ID3, SYLT, TALB, TIT2, TPE1, USLT

# MPEG1 Layer3, 128kbps, 44100Hz：帧长 = 144 * 128000 / 44100 = 417 字节
MPEG_FRAME = b"\xff\xfb\x90\x00" + b"\x00" * 413


def make_mp3_bytes(n: int = 20) -> bytes:
    return MPEG_FRAME * n


@pytest.fixture
def mp3_factory(tmp_path):
    """在临时目录里生成测试 mp3，可按需携带标签；返回文件路径。"""
    counter = {"i": 0}

    def _make(
        name: str = "song.mp3",
        *,
        title: str | None = None,
        artist: str | None = None,
        album: str | None = None,
        uslt: str | None = None,
        sylt: list | None = None,  # [(text, ms), ...]
        sylt_format: int = 2,
        raw: bytes | None = None,
    ):
        counter["i"] += 1
        path = tmp_path / f"{counter['i']:03d}-{name}"
        path.write_bytes(raw if raw is not None else make_mp3_bytes())
        if title or artist or album or uslt is not None or sylt is not None:
            tags = ID3()
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
            tags.save(str(path), v2_version=3)
        return path

    return _make

"""统一音频读取层测试：覆盖各容器的标签与内嵌歌词读取。"""

import pytest

from lyrics_transcriber import audio_file
from lyrics_transcriber.audio_file import (
    AudioError,
    load_audio,
    read_embedded_lyrics,
    read_tags,
)

LRC_TEXT = "[00:01.00]第一句\n[00:02.50]第二句"
PLAIN_TEXT = "第一行\n第二行"

REQUIRED_EXTENSIONS = {
    ".mp3", ".flac", ".wav", ".m4a", ".mp4", ".aac",
    ".ogg", ".opus", ".wma", ".ape", ".aiff", ".aif",
}


def test_supported_extensions_cover_required_formats():
    assert REQUIRED_EXTENSIONS <= audio_file.SUPPORTED_EXTENSIONS


# ----------------------------------------------------------------------
# 真实文件：flac / wav / aiff / aac


def test_load_flac_reads_tags_and_synced_lyrics(flac_factory):
    path = flac_factory(title="晴天", artist="周杰伦", album="叶惠美", lyrics=LRC_TEXT)
    audio = load_audio(path)
    assert (audio.tags.title, audio.tags.artist, audio.tags.album) == (
        "晴天", "周杰伦", "叶惠美",
    )
    assert audio.tags.duration_seconds > 0
    assert audio.embedded is not None
    assert audio.embedded.synced is True
    assert audio.embedded.source == "LYRICS"
    assert audio.embedded.lines == [(1000, "第一句"), (2500, "第二句")]


def test_load_flac_unsynced_field_plain(flac_factory):
    path = flac_factory(unsynced=PLAIN_TEXT)
    audio = load_audio(path)
    assert audio.embedded.source == "UNSYNCEDLYRICS"
    assert audio.embedded.synced is False
    assert audio.embedded.lines == ["第一行", "第二行"]


def test_load_flac_prefers_lyrics_over_unsynced(flac_factory):
    path = flac_factory(lyrics=LRC_TEXT, unsynced=PLAIN_TEXT)
    audio = load_audio(path)
    assert audio.embedded.source == "LYRICS"
    assert audio.embedded.synced is True


def test_load_flac_without_lyrics_has_no_embedded(flac_factory):
    path = flac_factory(title="只有标题")
    audio = load_audio(path)
    assert audio.embedded is None
    assert audio.tags.title == "只有标题"


def test_load_wav_reads_embedded_id3(wav_factory):
    path = wav_factory(title="歌", artist="人", album="专", uslt=LRC_TEXT)
    audio = load_audio(path)
    assert (audio.tags.title, audio.tags.artist, audio.tags.album) == ("歌", "人", "专")
    assert audio.embedded.source == "USLT"
    assert audio.embedded.synced is True
    assert audio.embedded.lines == [(1000, "第一句"), (2500, "第二句")]


def test_load_aiff_reads_embedded_sylt(aiff_factory):
    path = aiff_factory(title="歌", artist="人", sylt=[("第一句", 1000), ("第二句", 2500)])
    audio = load_audio(path)
    assert (audio.tags.title, audio.tags.artist) == ("歌", "人")
    assert audio.embedded.source == "SYLT"
    assert audio.embedded.synced is True
    assert audio.embedded.lines == [(1000, "第一句"), (2500, "第二句")]


def test_load_aiff_plain_uslt(aiff_factory):
    path = aiff_factory(uslt=PLAIN_TEXT)
    audio = load_audio(path)
    assert audio.embedded.synced is False
    assert audio.embedded.source == "USLT"


def test_load_aac_tagless_is_not_an_error(aac_factory):
    path = aac_factory()
    audio = load_audio(path)  # 不应抛异常
    assert audio.tags.title == ""
    assert audio.tags.duration_seconds > 0  # 裸流仍能拿到时长
    assert audio.embedded is None


def test_load_garbage_aac_is_tolerated(aac_factory):
    # 裸流 AAC 本身没有标签标准，读不出来也不该判为损坏
    path = aac_factory(raw=b"this is not a real aac stream at all")
    audio = load_audio(path)
    assert audio.embedded is None
    assert audio.tags.duration_seconds == 0.0


def test_load_corrupt_mp3_raises_audio_error(tmp_path):
    path = tmp_path / "bad.mp3"
    path.write_bytes(b"this is not an mp3 file at all")
    with pytest.raises(AudioError):
        load_audio(path)


def test_load_unknown_extension_raises_audio_error(tmp_path):
    path = tmp_path / "note.txt"
    path.write_text("hello", encoding="utf-8")
    with pytest.raises(AudioError):
        load_audio(path)


# ----------------------------------------------------------------------
# 逐容器读取层（真实标签对象；mp4 / ogg / opus / wma / ape）


@pytest.mark.parametrize(
    "builder_name, expected_source",
    [
        ("vorbis_tags", "LYRICS"),
        ("mp4_tags", "MP4"),
        ("asf_tags", "ASF"),
        ("ape_tags", "APE"),
    ],
)
def test_read_tags_and_lyrics_per_container(
    request, builder_name, expected_source
):
    builder = request.getfixturevalue(builder_name)
    tags = builder(title="标题", artist="歌手", album="专辑", lyrics=LRC_TEXT)

    parsed = read_tags(tags, 200.0)
    assert (parsed.title, parsed.artist, parsed.album) == ("标题", "歌手", "专辑")
    assert parsed.duration_seconds == 200.0

    embedded = read_embedded_lyrics(tags)
    assert embedded is not None
    assert embedded.source == expected_source
    assert embedded.synced is True
    assert embedded.lines == [(1000, "第一句"), (2500, "第二句")]


@pytest.mark.parametrize(
    "builder_name",
    ["vorbis_tags", "mp4_tags", "asf_tags", "ape_tags"],
)
def test_read_plain_lyrics_per_container(request, builder_name):
    builder = request.getfixturevalue(builder_name)
    tags = builder(lyrics=PLAIN_TEXT)
    embedded = read_embedded_lyrics(tags)
    assert embedded.synced is False
    assert embedded.lines == ["第一行", "第二行"]


@pytest.mark.parametrize(
    "builder_name",
    ["vorbis_tags", "mp4_tags", "asf_tags", "ape_tags"],
)
def test_read_empty_tags_gives_empty_result(request, builder_name):
    builder = request.getfixturevalue(builder_name)
    tags = builder()
    parsed = read_tags(tags, 12.0)
    assert (parsed.title, parsed.artist, parsed.album) == ("", "", "")
    assert parsed.duration_seconds == 12.0
    assert read_embedded_lyrics(tags) is None


def test_read_tags_with_none_returns_duration_only():
    parsed = read_tags(None, 42.0)
    assert parsed.title == "" and parsed.duration_seconds == 42.0


# ----------------------------------------------------------------------
# load_audio 全链路（mp4 / ogg / opus / wma / ape，用 patch_audio 模拟容器）


def test_load_m4a_via_adapter(tmp_path, patch_audio, mp4_tags):
    patch_audio(mp4_tags(title="歌", artist="人", album="专", lyrics=LRC_TEXT), length=210.0)
    audio = load_audio(tmp_path / "song.m4a")
    assert (audio.tags.title, audio.tags.artist, audio.tags.album) == ("歌", "人", "专")
    assert audio.tags.duration_seconds == 210.0
    assert audio.embedded.source == "MP4"
    assert audio.embedded.synced is True


def test_load_mp4_extension_via_adapter(tmp_path, patch_audio, mp4_tags):
    patch_audio(mp4_tags(lyrics=PLAIN_TEXT))
    audio = load_audio(tmp_path / "movie.mp4")
    assert audio.embedded.synced is False


def test_load_ogg_via_adapter(tmp_path, patch_audio, vorbis_tags):
    patch_audio(vorbis_tags(title="歌", artist="人", lyrics=LRC_TEXT))
    audio = load_audio(tmp_path / "song.ogg")
    assert (audio.tags.title, audio.tags.artist) == ("歌", "人")
    assert audio.embedded.source == "LYRICS"
    assert audio.embedded.synced is True


def test_load_opus_via_adapter(tmp_path, patch_audio, vorbis_tags):
    patch_audio(vorbis_tags(UNSYNCEDLYRICS=PLAIN_TEXT))
    audio = load_audio(tmp_path / "song.opus")
    assert audio.embedded.source == "UNSYNCEDLYRICS"
    assert audio.embedded.synced is False


def test_load_wma_via_adapter(tmp_path, patch_audio, asf_tags):
    patch_audio(asf_tags(title="歌", artist="人", lyrics=LRC_TEXT))
    audio = load_audio(tmp_path / "song.wma")
    assert (audio.tags.title, audio.tags.artist) == ("歌", "人")
    assert audio.embedded.source == "ASF"


def test_load_ape_via_adapter(tmp_path, patch_audio, ape_tags):
    patch_audio(ape_tags(title="歌", artist="人", lyrics=LRC_TEXT))
    audio = load_audio(tmp_path / "song.ape")
    assert (audio.tags.title, audio.tags.artist) == ("歌", "人")
    assert audio.embedded.source == "APE"


# ----------------------------------------------------------------------
# 回归：专辑歌手字段不得冒充歌手喂给在线搜索

def test_mp4_album_artist_not_used_as_artist(tmp_path, patch_audio, mp4_tags):
    """aART 是专辑歌手（合集里往往不是唱这首歌的人），不能兜底成歌手。"""
    tags = mp4_tags(album="合集合名")
    tags["aART"] = ["专辑歌手"]
    patch_audio(tags)
    audio = load_audio(tmp_path / "song.m4a")
    assert audio.tags.artist == ""
    assert audio.tags.album == "合集合名"


def test_asf_album_artist_not_used_as_artist(tmp_path, patch_audio, asf_tags):
    """WM/AlbumArtist 同理，不能兜底成歌手。"""
    from mutagen.asf import ASFUnicodeAttribute

    tags = asf_tags(album="合集合名")
    tags["WM/AlbumArtist"] = [ASFUnicodeAttribute("专辑歌手")]
    patch_audio(tags)
    audio = load_audio(tmp_path / "song.wma")
    assert audio.tags.artist == ""
    assert audio.tags.album == "合集合名"

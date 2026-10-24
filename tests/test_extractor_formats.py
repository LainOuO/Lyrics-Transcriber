"""跨格式全链路测试：内嵌歌词 → 生成同名 .lrc。"""

from pathlib import Path

from lyrics_transcriber import online
from lyrics_transcriber.extractor import LyricsTranscriber, Status
from lyrics_transcriber.online import OnlineLyrics

LRC_TEXT = "[00:01.00]第一句\n[00:02.50]第二句"
PLAIN_TEXT = "第一行\n第二行"


def _rename_to(path: Path, name: str) -> Path:
    """去掉夹具的序号前缀，得到干净的"歌手 - 标题.扩展名"文件名。"""
    target = path.with_name(name)
    path.rename(target)
    return target


# ----------------------------------------------------------------------
# 真实文件全链路


def test_flac_end_to_end_writes_same_name_lrc(flac_factory):
    path = _rename_to(
        flac_factory(title="晴天", artist="周杰伦", album="叶惠美", lyrics=LRC_TEXT),
        "晴天.flac",
    )
    result = LyricsTranscriber(use_online=False).process(path)

    assert result.status == Status.SYNCED
    assert result.source == "内嵌LYRICS"

    lrc = path.with_suffix(".lrc")
    assert lrc.name == "晴天.lrc"
    assert lrc.exists()
    # 关键：绝不能生成 晴天.flac.lrc
    assert not Path(str(path) + ".lrc").exists()

    text = lrc.read_text(encoding="utf-8")
    assert "[ti:晴天]" in text
    assert "[ar:周杰伦]" in text
    assert "[al:叶惠美]" in text
    assert "[00:01.00]第一句" in text
    assert "[00:02.50]第二句" in text


def test_flac_plain_lyrics_downgrades(flac_factory):
    path = flac_factory(title="歌", artist="人", unsynced=PLAIN_TEXT)
    result = LyricsTranscriber(use_online=False).process(path)
    assert result.status == Status.PLAIN
    assert result.source == "内嵌UNSYNCEDLYRICS"
    text = path.with_suffix(".lrc").read_text(encoding="utf-8")
    assert "第一行" in text
    assert "[00:" not in text


def test_wav_embedded_lrc_end_to_end(wav_factory):
    path = _rename_to(wav_factory(title="歌", artist="人", uslt=LRC_TEXT), "歌.wav")
    result = LyricsTranscriber(use_online=False).process(path)
    assert result.status == Status.SYNCED
    assert result.source == "内嵌USLT"
    lrc = path.with_suffix(".lrc")
    assert lrc.name == "歌.lrc"
    assert "[00:01.00]第一句" in lrc.read_text(encoding="utf-8")


def test_aiff_embedded_sylt_end_to_end(aiff_factory):
    path = _rename_to(
        aiff_factory(title="歌", artist="人", sylt=[("第一句", 1000), ("第二句", 2500)]),
        "歌.aiff",
    )
    result = LyricsTranscriber(use_online=False).process(path)
    assert result.status == Status.SYNCED
    assert result.source == "内嵌SYLT"
    text = path.with_suffix(".lrc").read_text(encoding="utf-8")
    assert "[ti:歌]" in text
    assert "[00:01.00]第一句" in text


def test_existing_flac_lrc_is_skipped_then_overwritten(flac_factory):
    path = flac_factory(title="歌", lyrics=LRC_TEXT)
    lrc = path.with_suffix(".lrc")
    lrc.write_text("旧的", encoding="utf-8")

    skipped = LyricsTranscriber(use_online=False).process(path)
    assert skipped.status == Status.SKIPPED
    assert lrc.read_text(encoding="utf-8") == "旧的"

    overwritten = LyricsTranscriber(use_online=False, overwrite=True).process(path)
    assert overwritten.status == Status.SYNCED
    assert "[00:01.00]第一句" in lrc.read_text(encoding="utf-8")


def test_corrupt_flac_reports_error(tmp_path):
    path = tmp_path / "bad.flac"
    path.write_bytes(b"definitely not a flac file")
    result = LyricsTranscriber(use_online=False).process(path)
    assert result.status == Status.ERROR
    assert "无法解析" in result.message


# ----------------------------------------------------------------------
# 无标签裸流 / 适配层模拟容器


def test_aac_tagless_uses_filename_fallback_and_online(aac_factory, monkeypatch):
    path = _rename_to(aac_factory(), "周杰伦 - 晴天.aac")
    captured = {}

    def fake_fetch(title, artist, album="", duration=None, timeout=None):
        captured.update(title=title, artist=artist)
        return OnlineLyrics(synced_text=LRC_TEXT, plain_text=None)

    monkeypatch.setattr(online, "fetch_lyrics", fake_fetch)
    result = LyricsTranscriber().process(path)

    assert captured == {"title": "晴天", "artist": "周杰伦"}
    assert result.status == Status.SYNCED
    assert result.source == "lrclib"
    text = path.with_suffix(".lrc").read_text(encoding="utf-8")
    assert "[ti:晴天]" in text
    assert "[00:01.00]第一句" in text


def test_aac_tagless_without_online_is_not_found_not_error(aac_factory):
    path = _rename_to(aac_factory(), "无名氏 - 无标签.aac")
    result = LyricsTranscriber(use_online=False).process(path)
    assert result.status == Status.NOT_FOUND
    assert result.status is not Status.ERROR
    assert not path.with_suffix(".lrc").exists()


def test_garbage_aac_does_not_error(aac_factory):
    path = _rename_to(aac_factory(raw=b"junk junk junk"), "坏文件.aac")
    result = LyricsTranscriber(use_online=False).process(path)
    assert result.status != Status.ERROR


def test_m4a_end_to_end_via_adapter(tmp_path, patch_audio, mp4_tags):
    path = tmp_path / "周杰伦 - 晴天.m4a"
    patch_audio(mp4_tags(title="晴天", artist="周杰伦", album="叶惠美", lyrics=LRC_TEXT))
    result = LyricsTranscriber(use_online=False).process(path)
    assert result.status == Status.SYNCED
    assert result.source == "内嵌MP4"
    lrc = path.with_suffix(".lrc")
    assert lrc.name == "周杰伦 - 晴天.lrc"
    text = lrc.read_text(encoding="utf-8")
    assert "[ti:晴天]" in text
    assert "[00:01.00]第一句" in text


def test_wma_end_to_end_via_adapter(tmp_path, patch_audio, asf_tags):
    path = tmp_path / "song.wma"
    patch_audio(asf_tags(title="歌", artist="人", lyrics=LRC_TEXT))
    result = LyricsTranscriber(use_online=False).process(path)
    assert result.status == Status.SYNCED
    assert result.source == "内嵌ASF"

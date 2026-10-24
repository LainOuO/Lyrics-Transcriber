from pathlib import Path

from lyrics_transcriber import online
from lyrics_transcriber.extractor import LyricsTranscriber, Status
from lyrics_transcriber.online import OnlineError, OnlineLyrics


def test_sylt_mp3_produces_synced_lrc(mp3_factory):
    path = mp3_factory(
        title="晴天", artist="周杰伦", album="叶惠美",
        sylt=[("第一句", 1000), ("第二句", 2500)],
    )
    result = LyricsTranscriber(use_online=False).process(path)
    assert result.status == Status.SYNCED
    assert result.source == "内嵌SYLT"
    text = path.with_suffix(".lrc").read_text(encoding="utf-8")
    assert "[ti:晴天]" in text
    assert "[00:01.00]第一句" in text


def test_uslt_plain_produces_plain_lrc(mp3_factory):
    path = mp3_factory(title="歌", artist="人", uslt="第一行\n第二行")
    result = LyricsTranscriber(use_online=False).process(path)
    assert result.status == Status.PLAIN
    assert result.source == "内嵌USLT"
    text = path.with_suffix(".lrc").read_text(encoding="utf-8")
    assert "第一行" in text
    assert "[00:" not in text


def test_no_lyrics_and_online_disabled(mp3_factory):
    path = mp3_factory(title="歌", artist="人")
    result = LyricsTranscriber(use_online=False).process(path)
    assert result.status == Status.NOT_FOUND
    assert not path.with_suffix(".lrc").exists()


def test_existing_lrc_skipped_then_overwritten(mp3_factory):
    path = mp3_factory(title="歌", sylt=[("A", 1000)])
    lrc = path.with_suffix(".lrc")
    lrc.write_text("旧的", encoding="utf-8")

    skipped = LyricsTranscriber(use_online=False).process(path)
    assert skipped.status == Status.SKIPPED
    assert lrc.read_text(encoding="utf-8") == "旧的"

    overwritten = LyricsTranscriber(use_online=False, overwrite=True).process(path)
    assert overwritten.status == Status.SYNCED
    assert "[00:01.00]A" in lrc.read_text(encoding="utf-8")


def test_filename_fallback_feeds_online_query(mp3_factory, monkeypatch):
    path = mp3_factory(name="x.mp3")  # 无任何标签
    # 重命名以避开夹具的序号前缀
    renamed = path.with_name("周杰伦 - 晴天.mp3")
    path.rename(renamed)
    path = renamed
    captured = {}

    def fake_fetch(title, artist, album="", duration=None, timeout=None):
        captured.update(title=title, artist=artist)
        return OnlineLyrics(synced_text="[00:01.00]A\n[00:02.00]B", plain_text=None)

    monkeypatch.setattr(online, "fetch_lyrics", fake_fetch)
    result = LyricsTranscriber().process(path)
    assert captured == {"title": "晴天", "artist": "周杰伦"}
    assert result.status == Status.SYNCED
    assert result.source == "lrclib"
    text = path.with_suffix(".lrc").read_text(encoding="utf-8")
    assert "[ti:晴天]" in text


def test_online_plain_lyrics_downgrades(mp3_factory, monkeypatch):
    path = mp3_factory(title="歌", artist="人")
    monkeypatch.setattr(
        online, "fetch_lyrics",
        lambda *a, **k: OnlineLyrics(synced_text=None, plain_text="纯文本\n两行"),
    )
    result = LyricsTranscriber().process(path)
    assert result.status == Status.PLAIN
    assert result.message  # 带有降级说明


def test_online_error_becomes_error_status(mp3_factory, monkeypatch):
    path = mp3_factory(title="歌", artist="人")

    def boom(*a, **k):
        raise OnlineError("断网了")

    monkeypatch.setattr(online, "fetch_lyrics", boom)
    result = LyricsTranscriber().process(path)
    assert result.status == Status.ERROR
    assert "断网了" in result.message


def test_instrumental_writes_nothing(mp3_factory, monkeypatch):
    path = mp3_factory(title="Interlude", artist="某人")
    monkeypatch.setattr(
        online, "fetch_lyrics",
        lambda *a, **k: OnlineLyrics(None, None, instrumental=True),
    )
    result = LyricsTranscriber().process(path)
    assert result.status == Status.INSTRUMENTAL
    assert not path.with_suffix(".lrc").exists()


def test_corrupt_mp3_reports_error(tmp_path):
    path = tmp_path / "bad.mp3"
    path.write_bytes(b"this is not an mp3 file at all")
    result = LyricsTranscriber(use_online=False).process(path)
    assert result.status == Status.ERROR
    assert "无法解析" in result.message


# ----------------------------------------------------------------------
# 回归：文件名回退要处理轨道号命名

def test_display_tags_strips_track_number_prefix():
    from lyrics_transcriber.id3_reader import TrackTags

    title, artist = LyricsTranscriber._display_tags(
        Path("01. 周杰伦 - 晴天.mp3"), TrackTags()
    )
    assert (title, artist) == ("晴天", "周杰伦")


def test_display_tags_drops_numeric_segment():
    from lyrics_transcriber.id3_reader import TrackTags

    title, artist = LyricsTranscriber._display_tags(
        Path("07 - 周杰伦 - 晴天.mp3"), TrackTags()
    )
    assert (title, artist) == ("晴天", "周杰伦")


def test_display_tags_keeps_title_with_dash():
    from lyrics_transcriber.id3_reader import TrackTags

    title, artist = LyricsTranscriber._display_tags(
        Path("周杰伦 - 恋曲1990 - Live.mp3"), TrackTags()
    )
    assert (title, artist) == ("恋曲1990 - Live", "周杰伦")


def test_display_tags_plain_title_with_track_number():
    from lyrics_transcriber.id3_reader import TrackTags

    title, artist = LyricsTranscriber._display_tags(Path("03. 晴天.mp3"), TrackTags())
    assert (title, artist) == ("晴天", "")


def test_display_tags_leading_digits_not_always_a_track_number():
    """歌名本身以数字开头（如 "7 rings"）不该被误剥。"""
    from lyrics_transcriber.id3_reader import TrackTags

    title, _ = LyricsTranscriber._display_tags(Path("7 rings.mp3"), TrackTags())
    assert title == "7 rings"


# ----------------------------------------------------------------------
# AI 听歌转写兜底

import pytest

import lyrics_transcriber.ai_transcribe as ai_module
from lyrics_transcriber.ai_transcribe import Transcription, TranscribeError


@pytest.fixture
def fake_transcribe(monkeypatch):
    """替换 AI 转写主入口，记录调用；默认转出带时间轴的两行。"""
    calls = []

    def _fake(path, settings):
        calls.append(path)
        return Transcription(
            text="第一行 第二行",
            lines=[(1000, "第一行"), (3000, "第二行")],
            plain_lines=["第一行", "第二行"],
        )

    monkeypatch.setattr(ai_module, "transcribe_audio", _fake)
    return calls


def test_ai_off_by_default_never_transcribes(mp3_factory, monkeypatch):
    """默认不开 AI 兜底：行为与旧版完全一致（回归守护）。"""
    def boom(path, settings):
        raise AssertionError("use_ai=False 不该触发 AI 转写")

    monkeypatch.setattr(ai_module, "transcribe_audio", boom)
    path = mp3_factory(name="bare.mp3")
    result = LyricsTranscriber(use_online=False).process(path)
    assert result.status == Status.NOT_FOUND
    assert not path.with_suffix(".lrc").exists()


def test_ai_synced_transcription_writes_synced_lrc(mp3_factory, fake_transcribe):
    path = mp3_factory(title="歌", artist="人")
    result = LyricsTranscriber(use_online=False, use_ai=True).process(path)
    assert result.status == Status.SYNCED
    assert result.source == "AI 转写"
    text = path.with_suffix(".lrc").read_text(encoding="utf-8")
    assert "[00:01.00]第一行" in text
    assert "[00:03.00]第二行" in text


def test_ai_plain_transcription_downgrades(mp3_factory, monkeypatch):
    monkeypatch.setattr(
        ai_module, "transcribe_audio",
        lambda path, settings: Transcription(text="纯文本", plain_lines=["纯文本"]),
    )
    path = mp3_factory(title="歌", artist="人")
    result = LyricsTranscriber(use_online=False, use_ai=True).process(path)
    assert result.status == Status.PLAIN
    assert result.source == "AI 转写"
    assert "来源无时间轴" in result.message


def test_ai_empty_transcription_reports_not_found(mp3_factory, monkeypatch):
    """纯音乐转不出词：如实报「未识别」，不算处理失败。"""
    monkeypatch.setattr(
        ai_module, "transcribe_audio", lambda path, settings: Transcription(text="  ")
    )
    path = mp3_factory(title="歌", artist="人")
    result = LyricsTranscriber(use_online=False, use_ai=True).process(path)
    assert result.status == Status.NOT_FOUND
    assert "未识别出人声歌词" in result.message
    assert not path.with_suffix(".lrc").exists()


def test_ai_transcribe_error_becomes_error(mp3_factory, monkeypatch):
    def raise_error(path, settings):
        raise TranscribeError("密钥无效（401）")

    monkeypatch.setattr(ai_module, "transcribe_audio", raise_error)
    path = mp3_factory(title="歌", artist="人")
    result = LyricsTranscriber(use_online=False, use_ai=True).process(path)
    assert result.status == Status.ERROR
    assert "AI 转写失败" in result.message
    assert "密钥无效" in result.message


def test_ai_confirm_cb_skip_keeps_nothing(mp3_factory, fake_transcribe):
    """预览里点了「跳过」：不写文件，也不算 AI 转写失败。"""
    path = mp3_factory(title="歌", artist="人")
    result = LyricsTranscriber(use_online=False, use_ai=True,
                             confirm_cb=lambda path, lines, synced: None).process(path)
    assert result.status == Status.NOT_FOUND
    assert "跳过" in result.message
    assert not path.with_suffix(".lrc").exists()


def test_ai_confirm_cb_edited_lines_are_written(mp3_factory, fake_transcribe):
    """预览里改错字后的内容才是最终写进 .lrc 的内容。"""
    path = mp3_factory(title="歌", artist="人")
    result = LyricsTranscriber(
        use_online=False, use_ai=True,
        confirm_cb=lambda path, lines, synced: ([(2000, "改过的词")], True),
    ).process(path)
    assert result.status == Status.SYNCED
    text = path.with_suffix(".lrc").read_text(encoding="utf-8")
    assert "改过的词" in text
    assert "第一行" not in text


def test_ai_runs_without_title_when_online_disabled(mp3_factory, fake_transcribe):
    """AI 听的是音频本身：没有标题、在线兜底关着也照样能转。"""
    path = mp3_factory(name="无名.mp3")
    result = LyricsTranscriber(use_online=False, use_ai=True).process(path)
    assert result.status == Status.SYNCED
    assert result.source == "AI 转写"
    assert len(fake_transcribe) == 1


def test_ai_skipped_when_embedded_lyrics_exist(mp3_factory, fake_transcribe):
    mp3_factory(title="歌", artist="人", sylt=[("内嵌", 1000)])
    assert fake_transcribe == []  # 内嵌有词，根本轮不到 AI


def test_ai_skipped_when_online_found(mp3_factory, fake_transcribe, monkeypatch):
    monkeypatch.setattr(
        online, "fetch_lyrics",
        lambda *a, **k: OnlineLyrics(
            synced_text="[00:05.00]在线的词\n[00:07.00]在线的第二行", plain_text=None),
    )
    path = mp3_factory(title="歌", artist="人")
    result = LyricsTranscriber(use_ai=True).process(path)
    assert result.source == "lrclib"
    assert fake_transcribe == []


def test_ai_runs_after_online_not_found(mp3_factory, fake_transcribe, monkeypatch):
    monkeypatch.setattr(online, "fetch_lyrics", lambda *a, **k: None)
    path = mp3_factory(title="歌", artist="人")
    result = LyricsTranscriber(use_ai=True).process(path)
    assert result.source == "AI 转写"
    assert result.status == Status.SYNCED


def test_ai_skipped_when_online_error(mp3_factory, fake_transcribe, monkeypatch):
    """在线查询网络出错时，如实报错，不顺手乱转（云端 AI 同样需要网络）。"""
    def boom(*a, **k):
        raise OnlineError("网络请求失败: 断网了")

    monkeypatch.setattr(online, "fetch_lyrics", boom)
    path = mp3_factory(title="歌", artist="人")
    result = LyricsTranscriber(use_ai=True).process(path)
    assert result.status == Status.ERROR
    assert fake_transcribe == []


def test_ai_skipped_when_online_says_instrumental(mp3_factory, fake_transcribe, monkeypatch):
    """lrclib 都标了纯音乐，没必要再花一次转写。"""
    monkeypatch.setattr(
        online, "fetch_lyrics",
        lambda *a, **k: OnlineLyrics(synced_text=None, plain_text=None, instrumental=True),
    )
    path = mp3_factory(title="前奏", artist="人")
    result = LyricsTranscriber(use_ai=True).process(path)
    assert result.status == Status.INSTRUMENTAL
    assert fake_transcribe == []

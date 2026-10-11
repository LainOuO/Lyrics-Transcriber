from lyrics_extractor import online
from lyrics_extractor.extractor import LyricsExtractor, Status
from lyrics_extractor.online import OnlineError, OnlineLyrics


def test_sylt_mp3_produces_synced_lrc(mp3_factory):
    path = mp3_factory(
        title="晴天", artist="周杰伦", album="叶惠美",
        sylt=[("第一句", 1000), ("第二句", 2500)],
    )
    result = LyricsExtractor(use_online=False).process(path)
    assert result.status == Status.SYNCED
    assert result.source == "内嵌SYLT"
    text = path.with_suffix(".lrc").read_text(encoding="utf-8")
    assert "[ti:晴天]" in text
    assert "[00:01.00]第一句" in text


def test_uslt_plain_produces_plain_lrc(mp3_factory):
    path = mp3_factory(title="歌", artist="人", uslt="第一行\n第二行")
    result = LyricsExtractor(use_online=False).process(path)
    assert result.status == Status.PLAIN
    assert result.source == "内嵌USLT"
    text = path.with_suffix(".lrc").read_text(encoding="utf-8")
    assert "第一行" in text
    assert "[00:" not in text


def test_no_lyrics_and_online_disabled(mp3_factory):
    path = mp3_factory(title="歌", artist="人")
    result = LyricsExtractor(use_online=False).process(path)
    assert result.status == Status.NOT_FOUND
    assert not path.with_suffix(".lrc").exists()


def test_existing_lrc_skipped_then_overwritten(mp3_factory):
    path = mp3_factory(title="歌", sylt=[("A", 1000)])
    lrc = path.with_suffix(".lrc")
    lrc.write_text("旧的", encoding="utf-8")

    skipped = LyricsExtractor(use_online=False).process(path)
    assert skipped.status == Status.SKIPPED
    assert lrc.read_text(encoding="utf-8") == "旧的"

    overwritten = LyricsExtractor(use_online=False, overwrite=True).process(path)
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
    result = LyricsExtractor().process(path)
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
    result = LyricsExtractor().process(path)
    assert result.status == Status.PLAIN
    assert result.message  # 带有降级说明


def test_online_error_becomes_error_status(mp3_factory, monkeypatch):
    path = mp3_factory(title="歌", artist="人")

    def boom(*a, **k):
        raise OnlineError("断网了")

    monkeypatch.setattr(online, "fetch_lyrics", boom)
    result = LyricsExtractor().process(path)
    assert result.status == Status.ERROR
    assert "断网了" in result.message


def test_instrumental_writes_nothing(mp3_factory, monkeypatch):
    path = mp3_factory(title="Interlude", artist="某人")
    monkeypatch.setattr(
        online, "fetch_lyrics",
        lambda *a, **k: OnlineLyrics(None, None, instrumental=True),
    )
    result = LyricsExtractor().process(path)
    assert result.status == Status.INSTRUMENTAL
    assert not path.with_suffix(".lrc").exists()


def test_corrupt_mp3_reports_error(tmp_path):
    path = tmp_path / "bad.mp3"
    path.write_bytes(b"this is not an mp3 file at all")
    result = LyricsExtractor(use_online=False).process(path)
    assert result.status == Status.ERROR
    assert "无法解析" in result.message

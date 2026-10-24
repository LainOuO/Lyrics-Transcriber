from lyrics_transcriber import id3_reader


def test_read_tags(mp3_factory):
    path = mp3_factory(title="晴天", artist="周杰伦", album="叶惠美")
    mp3 = id3_reader.load_mp3(path)
    tags = id3_reader.read_tags(mp3)
    assert tags.title == "晴天"
    assert tags.artist == "周杰伦"
    assert tags.album == "叶惠美"
    assert tags.duration_seconds > 0


def test_read_tags_bare_file_has_empty_fields(mp3_factory):
    path = mp3_factory()
    mp3 = id3_reader.load_mp3(path)
    tags = id3_reader.read_tags(mp3)
    assert tags.title == ""
    assert tags.artist == ""
    assert tags.album == ""
    assert tags.duration_seconds > 0


def test_sylt_returns_sorted_synced_lines(mp3_factory):
    path = mp3_factory(sylt=[("第二句", 2500), ("第一句", 1000), ("第三句", 43210)])
    mp3 = id3_reader.load_mp3(path)
    lyrics = id3_reader.read_embedded_lyrics(mp3.tags)
    assert lyrics is not None
    assert lyrics.synced is True
    assert lyrics.source == "SYLT"
    assert lyrics.lines == [(1000, "第一句"), (2500, "第二句"), (43210, "第三句")]


def test_sylt_millisecond_precision_kept(mp3_factory):
    path = mp3_factory(sylt=[("A", 1234), ("B", 999)])
    lyrics = id3_reader.read_embedded_lyrics(id3_reader.load_mp3(path).tags)
    assert lyrics.lines == [(999, "B"), (1234, "A")]


def test_sylt_with_embedded_newlines_split(mp3_factory):
    path = mp3_factory(sylt=[("两行\n合一帧", 1000)])
    lyrics = id3_reader.read_embedded_lyrics(id3_reader.load_mp3(path).tags)
    assert lyrics.lines == [(1000, "两行"), (1000, "合一帧")]


def test_sylt_frame_based_falls_back_to_uslt(mp3_factory):
    # format=1（MPEG 帧数）无法换算成时间轴，应回退到 USLT
    path = mp3_factory(
        sylt=[("帧歌词", 100)],
        sylt_format=1,
        uslt="普通歌词",
    )
    lyrics = id3_reader.read_embedded_lyrics(id3_reader.load_mp3(path).tags)
    assert lyrics is not None
    assert lyrics.source == "USLT"
    assert lyrics.synced is False


def test_uslt_plain_text(mp3_factory):
    path = mp3_factory(uslt="第一行\n第二行\n\n第三行")
    lyrics = id3_reader.read_embedded_lyrics(id3_reader.load_mp3(path).tags)
    assert lyrics.synced is False
    assert lyrics.source == "USLT"
    assert lyrics.lines == ["第一行", "第二行", "第三行"]


def test_uslt_containing_lrc_becomes_synced(mp3_factory):
    path = mp3_factory(uslt="[00:01.00]A\n[00:02.50]B\n[01:02]C")
    lyrics = id3_reader.read_embedded_lyrics(id3_reader.load_mp3(path).tags)
    assert lyrics.synced is True
    assert lyrics.lines == [(1000, "A"), (2500, "B"), (62000, "C")]


def test_uslt_multiple_stamps_one_line(mp3_factory):
    path = mp3_factory(uslt="[00:01.00][00:05.00]副歌\n[00:30.00]尾声")
    lyrics = id3_reader.read_embedded_lyrics(id3_reader.load_mp3(path).tags)
    assert lyrics.synced is True
    assert lyrics.lines == [(1000, "副歌"), (5000, "副歌"), (30000, "尾声")]


def test_uslt_mostly_plain_stays_plain(mp3_factory):
    # 只有少量行带时间戳时，整体按纯文本处理，避免丢行
    text = "[00:01.00]A\n普通行1\n普通行2\n普通行3\n普通行4"
    path = mp3_factory(uslt=text)
    lyrics = id3_reader.read_embedded_lyrics(id3_reader.load_mp3(path).tags)
    assert lyrics.synced is False
    assert lyrics.lines[0] == "[00:01.00]A"


def test_no_lyrics_frames_returns_none(mp3_factory):
    path = mp3_factory(title="有标题没歌词")
    lyrics = id3_reader.read_embedded_lyrics(id3_reader.load_mp3(path).tags)
    assert lyrics is None


def test_none_tags_returns_none():
    assert id3_reader.read_embedded_lyrics(None) is None

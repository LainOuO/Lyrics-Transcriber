from lyrics_extractor import lrc_writer
from lyrics_extractor.lrc_writer import build_lrc, format_timestamp, parse_lrc_text


def test_parse_lrc_text_synced():
    lines = parse_lrc_text("[00:01.00]A\n[00:02.50]B\n[01:02]C")
    assert lines == [(1000, "A"), (2500, "B"), (62000, "C")]


def test_parse_lrc_text_plain_returns_none():
    assert parse_lrc_text("第一行\n第二行") is None


def test_parse_lrc_text_empty_returns_none():
    assert parse_lrc_text("") is None
    assert parse_lrc_text("\n  \n") is None


def test_format_timestamp_basics():
    assert format_timestamp(0) == "00:00.00"
    assert format_timestamp(1000) == "00:01.00"
    assert format_timestamp(61234) == "01:01.23"
    assert format_timestamp(3599999) == "59:59.99"
    assert format_timestamp(3600000) == "60:00.00"


def test_format_timestamp_clamps_negative():
    assert format_timestamp(-5) == "00:00.00"


def test_build_lrc_synced_sorted_and_deduped():
    content = build_lrc(
        "晴天",
        "周杰伦",
        "叶惠美",
        [(2500, "第二句"), (1000, "第一句"), (1000, "第一句")],
        synced=True,
    )
    lines = content.splitlines()
    assert lines[0] == "[ti:晴天]"
    assert lines[1] == "[ar:周杰伦]"
    assert lines[2] == "[al:叶惠美]"
    assert lines[3].startswith("[by:")
    assert lines[4] == "[00:01.00]第一句"
    assert lines[5] == "[00:02.50]第二句"
    assert len(lines) == 6


def test_build_lrc_synced_empty_text_keeps_stamp():
    content = build_lrc("", "", "", [(5000, "")], synced=True)
    assert content.splitlines()[-1] == "[00:05.00]"


def test_build_lrc_plain_has_no_stamps():
    content = build_lrc("歌名", "歌手", "", ["第一行", "第二行"], synced=False)
    lines = content.splitlines()
    assert lines[0] == "[ti:歌名]"
    assert lines[1] == "[ar:歌手]"
    assert lines[2].startswith("[by:")
    assert lines[3] == "第一行"
    assert lines[4] == "第二行"
    assert "[" not in lines[3] and "[" not in lines[4]


def test_lrc_path_for_replaces_suffix(tmp_path):
    mp3 = tmp_path / "song.mp3"
    assert lrc_writer.lrc_path_for(mp3) == tmp_path / "song.lrc"


def test_write_lrc_writes_utf8_next_to_mp3(tmp_path):
    mp3 = tmp_path / "晴天.mp3"
    mp3.write_bytes(b"\x00")
    out = lrc_writer.write_lrc(mp3, "晴天", "周杰伦", "叶惠美", [(1000, "第一句")], True)
    assert out == tmp_path / "晴天.lrc"
    text = out.read_text(encoding="utf-8")
    assert "[ti:晴天]" in text
    assert "[00:01.00]第一句" in text
    assert text.endswith("\n")


def test_parse_lrc_text_strips_enhanced_word_stamps():
    """增强型 LRC 的逐字时间戳 <mm:ss.xx> 不该残留在歌词正文里。"""
    lines = parse_lrc_text("[00:01.00]<00:01.20>逐<00:01.50>字\n[00:30.00]副歌")
    assert lines == [(1000, "逐字"), (30000, "副歌")]


def test_parse_lrc_text_word_stamps_without_line_stamps_stays_plain():
    """只有逐字时间戳、没有行时间戳的文本仍按纯文本处理（剥掉尖括号戳）。"""
    text = "普通行1\n<00:01.20>逐<00:01.50>字\n普通行2\n普通行3"
    lines = parse_lrc_text(text)
    assert lines is None

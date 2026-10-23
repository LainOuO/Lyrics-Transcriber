import threading

from lyrics_extractor.audio_file import SUPPORTED_EXTENSIONS
from lyrics_extractor.batch import (
    export_failure_report,
    is_supported_audio,
    run_batch,
    run_files,
    scan_audio_files,
    scan_mp3s,
)


def make_folder(tmp_path, mp3_factory, n_bare=1):
    """造一个测试歌单：1 个带同步歌词、1 个带纯文本歌词、n 个没有任何歌词。"""
    files = [
        mp3_factory(name="synced.mp3", title="同步", artist="甲", sylt=[("A", 1000)]),
        mp3_factory(name="plain.mp3", title="纯文", artist="乙", uslt="行1\n行2"),
    ]
    for i in range(n_bare):
        files.append(mp3_factory(name=f"bare{i}.mp3"))
    return files


def test_run_batch_counts_and_failures(tmp_path, mp3_factory):
    make_folder(tmp_path, mp3_factory)
    logs = []
    summary = run_batch(tmp_path, use_online=False, log_cb=logs.append)
    assert summary.total == 3
    assert summary.synced == 1
    assert summary.plain == 1
    assert summary.not_found == 1
    assert summary.written == 2
    assert len(summary.failures) == 1
    assert "bare" in summary.failures[0][0]
    assert len(logs) == 3
    assert logs[0].startswith("[1/3]")


def test_progress_callback_reports_each_file(tmp_path, mp3_factory):
    make_folder(tmp_path, mp3_factory, n_bare=0)
    seen = []
    run_batch(
        tmp_path,
        use_online=False,
        progress_cb=lambda done, total, result=None: seen.append((done, total)),
    )
    assert seen == [(1, 2), (2, 2)]


def test_recursive_scan(tmp_path, mp3_factory):
    make_folder(tmp_path, mp3_factory, n_bare=0)
    sub = tmp_path / "子目录"
    sub.mkdir()
    mp3_factory(name="sub.mp3", title="子", artist="人", sylt=[("A", 1000)], dirpath=sub)
    assert len(scan_mp3s(tmp_path, recursive=True)) == 3
    assert len(scan_mp3s(tmp_path, recursive=False)) == 2

    summary = run_batch(tmp_path, use_online=False, recursive=False)
    assert summary.total == 2


def test_stop_event_stops_early(tmp_path, mp3_factory):
    make_folder(tmp_path, mp3_factory)
    event = threading.Event()
    event.set()
    summary = run_batch(tmp_path, use_online=False, stop_event=event)
    assert summary.stopped is True
    assert summary.written == 0


def test_stop_event_set_midway(tmp_path, mp3_factory):
    make_folder(tmp_path, mp3_factory)
    event = threading.Event()

    def stop_after_first(done, total, result=None):
        if done == 1:
            event.set()

    summary = run_batch(
        tmp_path, use_online=False, stop_event=event, progress_cb=stop_after_first
    )
    assert summary.stopped is True
    assert summary.written == 1


def test_skipped_lrc_counted(tmp_path, mp3_factory):
    files = make_folder(tmp_path, mp3_factory, n_bare=0)
    files[0].with_suffix(".lrc").write_text("旧的", encoding="utf-8")
    summary = run_batch(tmp_path, use_online=False)
    assert summary.skipped == 1
    assert summary.plain == 1


def test_empty_folder(tmp_path):
    summary = run_batch(tmp_path)
    assert summary.total == 0
    assert not summary.stopped


def test_export_failure_report(tmp_path, mp3_factory):
    make_folder(tmp_path, mp3_factory)
    summary = run_batch(tmp_path, use_online=False)
    out = export_failure_report(summary, tmp_path / "失败清单.txt")
    text = out.read_text(encoding="utf-8-sig")
    assert "bare" in text
    assert "未找到歌词" in text


# ----------------------------------------------------------------------
# 扩展名集合扫描


def test_scan_audio_files_covers_every_supported_extension(tmp_path):
    for ext in sorted(SUPPORTED_EXTENSIONS):
        (tmp_path / f"song{ext}").write_bytes(b"\x00")
    (tmp_path / "note.txt").write_bytes(b"\x00")
    (tmp_path / "cover.jpg").write_bytes(b"\x00")

    found = scan_audio_files(tmp_path)
    assert len(found) == len(SUPPORTED_EXTENSIONS)
    assert {p.suffix.lower() for p in found} == set(SUPPORTED_EXTENSIONS)
    assert all(p.suffix.lower() in SUPPORTED_EXTENSIONS for p in found)


def test_scan_audio_files_is_case_insensitive(tmp_path):
    (tmp_path / "A.MP3").write_bytes(b"\x00")
    (tmp_path / "b.FlAc").write_bytes(b"\x00")
    (tmp_path / "c.Ogg").write_bytes(b"\x00")
    (tmp_path / "d.txt").write_bytes(b"\x00")

    assert {p.name for p in scan_audio_files(tmp_path)} == {"A.MP3", "b.FlAc", "c.Ogg"}


def test_scan_audio_files_recursive_switch(tmp_path):
    (tmp_path / "top.flac").write_bytes(b"\x00")
    sub = tmp_path / "子目录"
    sub.mkdir()
    (sub / "deep.wav").write_bytes(b"\x00")

    assert len(scan_audio_files(tmp_path, recursive=True)) == 2
    assert len(scan_audio_files(tmp_path, recursive=False)) == 1


def test_scan_audio_files_missing_folder_returns_empty(tmp_path):
    assert scan_audio_files(tmp_path / "不存在") == []


def test_scan_mp3s_is_backwards_compatible_alias():
    assert scan_mp3s is scan_audio_files


def test_run_batch_processes_mixed_formats(tmp_path, mp3_factory, flac_factory, wav_factory):
    mp3_factory(name="a.mp3", title="甲", sylt=[("A", 1000)])
    flac_factory(name="b.flac", title="乙", lyrics="[00:01.00]B\n[00:02.00]C")
    wav_factory(name="c.wav", title="丙", uslt="行1\n行2")

    summary = run_batch(tmp_path, use_online=False)
    assert summary.total == 3
    assert summary.synced == 2
    assert summary.plain == 1
    assert summary.errors == 0
    assert len(list(tmp_path.glob("*.lrc"))) == 3


# ----------------------------------------------------------------------
# 指定文件列表（界面的「单个文件」模式）


def test_run_files_processes_only_given_files(tmp_path, mp3_factory):
    """只处理传进来的文件，同目录下的其它音频不受影响。"""
    target = mp3_factory(name="target.mp3", title="目标", sylt=[("A", 1000)])
    other = mp3_factory(name="other.mp3", title="别的", sylt=[("A", 1000)])

    summary = run_files([target], use_online=False)
    assert summary.total == 1
    assert summary.synced == 1
    assert target.with_suffix(".lrc").exists()
    assert not other.with_suffix(".lrc").exists()


def test_run_files_single_file_reports_one_of_one(tmp_path, mp3_factory):
    target = mp3_factory(name="solo.mp3", title="独", sylt=[("A", 1000)])
    logs, seen = [], []
    summary = run_files(
        [target],
        use_online=False,
        log_cb=logs.append,
        progress_cb=lambda done, total, result=None: seen.append((done, total)),
    )
    assert summary.total == 1
    assert seen == [(1, 1)]
    assert logs[0].startswith("[1/1] ")
    assert "solo.mp3" in logs[0]


def test_run_files_accepts_str_paths(tmp_path, mp3_factory):
    target = mp3_factory(name="asstring.mp3", title="串", sylt=[("A", 1000)])
    summary = run_files([str(target)], use_online=False)
    assert summary.total == 1
    assert summary.synced == 1
    assert summary.failures == []


def test_run_files_empty_list(tmp_path):
    summary = run_files([], use_online=False)
    assert summary.total == 0
    assert not summary.stopped


def test_run_files_honours_skip_and_overwrite(tmp_path, mp3_factory):
    target = mp3_factory(name="skip.mp3", title="跳", sylt=[("A", 1000)])
    target.with_suffix(".lrc").write_text("旧的", encoding="utf-8")

    assert run_files([target], use_online=False).skipped == 1
    assert run_files([target], use_online=False, overwrite=True).synced == 1


def test_run_files_collects_failures(tmp_path, mp3_factory):
    """单文件模式同样要能导出失败清单。"""
    target = mp3_factory(name="bare.mp3")
    summary = run_files([target], use_online=False)
    assert summary.not_found == 1
    assert len(summary.failures) == 1
    out = export_failure_report(summary, tmp_path / "失败清单.txt")
    assert "bare.mp3" in out.read_text(encoding="utf-8-sig")


def test_run_files_stop_event(tmp_path, mp3_factory):
    target = mp3_factory(name="stop.mp3", title="停", sylt=[("A", 1000)])
    event = threading.Event()
    event.set()
    summary = run_files([target], use_online=False, stop_event=event)
    assert summary.stopped is True
    assert summary.written == 0


# ----------------------------------------------------------------------
# 单文件格式校验


def test_is_supported_audio_accepts_supported_extensions(tmp_path):
    for ext in sorted(SUPPORTED_EXTENSIONS):
        path = tmp_path / f"song{ext}"
        path.write_bytes(b"\x00")
        assert is_supported_audio(path) is True


def test_is_supported_audio_is_case_insensitive(tmp_path):
    path = tmp_path / "SONG.MP3"
    path.write_bytes(b"\x00")
    assert is_supported_audio(path) is True


def test_is_supported_audio_rejects_other_files(tmp_path):
    txt = tmp_path / "note.txt"
    txt.write_bytes(b"\x00")
    no_ext = tmp_path / "README"
    no_ext.write_bytes(b"\x00")
    folder = tmp_path / "album.flac"  # 名字像音频，其实是目录
    folder.mkdir()

    assert is_supported_audio(txt) is False
    assert is_supported_audio(no_ext) is False
    assert is_supported_audio(folder) is False
    assert is_supported_audio(tmp_path / "不存在.mp3") is False


def test_is_supported_audio_accepts_str_path(tmp_path):
    path = tmp_path / "str.mp3"
    path.write_bytes(b"\x00")
    assert is_supported_audio(str(path)) is True


# ----------------------------------------------------------------------
# AI 兜底参数透传


def test_run_files_passes_ai_options_to_extractor(tmp_path, mp3_factory, monkeypatch):
    """界面勾选的 AI 选项要原样传到流水线。"""
    from pathlib import Path as _Path

    from lyrics_extractor import batch as batch_mod
    from lyrics_extractor.extractor import ExtractResult, Status

    seen = {}

    class FakeExtractor:
        def __init__(self, **kwargs):
            seen.update(kwargs)

        def process(self, path):
            return ExtractResult(_Path(path), Status.NOT_FOUND)

    monkeypatch.setattr(batch_mod, "LyricsExtractor", FakeExtractor)
    target = mp3_factory(name="bare.mp3")
    run_files(
        [target], use_online=False, use_ai=True,
        ai_settings="设置对象", confirm_cb=lambda *a: None,
    )
    assert seen["use_ai"] is True
    assert seen["ai_settings"] == "设置对象"
    assert callable(seen["confirm_cb"])
    # 默认关闭时也原样传递（False / None）
    seen.clear()
    run_files([target], use_online=False)
    assert seen["use_ai"] is False
    assert seen["ai_settings"] is None


def test_run_batch_forwards_ai_options(tmp_path, mp3_factory, monkeypatch):
    from lyrics_extractor import batch as batch_mod
    from lyrics_extractor.batch import RunSummary

    seen = {}

    def fake_run_files(files, **kwargs):
        seen["files"] = list(files)
        seen["ai"] = (kwargs.get("use_ai"), kwargs.get("ai_settings"),
                      kwargs.get("confirm_cb"))
        return RunSummary(total=len(files))

    monkeypatch.setattr(batch_mod, "run_files", fake_run_files)
    mp3_factory(name="a.mp3")
    run_batch(tmp_path, use_ai=True, ai_settings="S", confirm_cb="cb")
    assert seen["ai"] == (True, "S", "cb")


def test_run_files_ai_fallback_writes_lrc_end_to_end(tmp_path, mp3_factory, monkeypatch):
    """全链路：无内嵌无在线 → AI 转写 → 写出带时间轴的 .lrc。"""
    from lyrics_extractor import ai_transcribe

    target = mp3_factory(name="bare.mp3")
    monkeypatch.setattr(
        ai_transcribe, "transcribe_audio",
        lambda path, settings: ai_transcribe.Transcription(
            text="AI 第一行 AI 第二行",
            lines=[(1000, "AI 第一行"), (3000, "AI 第二行")],
            plain_lines=["AI 第一行", "AI 第二行"],
        ),
    )
    summary = run_files([target], use_online=False, use_ai=True)
    assert summary.synced == 1
    assert summary.failures == []
    text = target.with_suffix(".lrc").read_text(encoding="utf-8")
    assert "[00:01.00]AI 第一行" in text

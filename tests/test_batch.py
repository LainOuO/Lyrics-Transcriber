import threading

from lyrics_extractor.batch import export_failure_report, run_batch, scan_mp3s


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

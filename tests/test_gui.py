import pytest

tkinter = pytest.importorskip("tkinter")

from lyrics_extractor import batch
from lyrics_extractor.batch import RunSummary
from lyrics_extractor.extractor import Status
from lyrics_extractor.gui import App


@pytest.fixture
def app(monkeypatch):
    try:
        root = tkinter.Tk()
    except Exception:  # 无显示环境时跳过
        pytest.skip("无法创建 Tk 窗口")
    root.withdraw()  # 冒烟测试不真正弹窗
    gui = App(root)
    # 弹窗一律替换为记录，避免模态对话框阻塞测试
    dialogs = []
    monkeypatch.setattr(
        "lyrics_extractor.gui.messagebox.showwarning",
        lambda *a, **k: dialogs.append(("warning", a)),
    )
    monkeypatch.setattr(
        "lyrics_extractor.gui.messagebox.showerror",
        lambda *a, **k: dialogs.append(("error", a)),
    )
    monkeypatch.setattr(
        "lyrics_extractor.gui.messagebox.showinfo",
        lambda *a, **k: dialogs.append(("info", a)),
    )
    gui._dialogs = dialogs
    root.update()
    yield gui
    root.destroy()


def test_window_builds_and_defaults(app):
    assert app.overwrite_var.get() is False
    assert app.recursive_var.get() is True
    assert app.online_var.get() is True
    assert str(app.start_btn["state"]) == "normal"
    assert str(app.stop_btn["state"]) == "disabled"


def test_start_rejects_missing_folder(app):
    app.folder_var.set(r"Z:\不存在的目录\不存在")
    app._start()  # 应记录警告弹窗并直接返回，不启动线程
    assert app.worker is None
    assert app._dialogs == [("warning", ("歌词提取器", "请先选择一个存在的歌曲文件夹"))]


def test_on_done_updates_summary_line(app):
    summary = RunSummary(total=5, synced=2, plain=1, not_found=1, instrumental=0,
                         skipped=1, errors=0,
                         failures=[("x.mp3", Status.NOT_FOUND.value, "内嵌无歌词")])
    app._on_done(summary)
    assert "共 5 个" in app.status_var.get()
    assert "同步 2 / 纯文本 1" in app.status_var.get()
    assert str(app.export_btn["state"]) == "normal"
    assert app.summary is summary


def test_on_done_without_failures_disables_export(app):
    app._on_done(RunSummary(total=1, synced=1))
    assert str(app.export_btn["state"]) == "disabled"


def test_on_done_marks_stopped(app):
    summary = RunSummary(total=3, stopped=True, synced=1)
    app._on_done(summary)
    assert "已停止" in app.status_var.get()


def test_log_view_inserts_lines(app):
    app._log("第一行")
    app._log("第二行")
    content = app.log_text.get("1.0", "end")
    assert "第一行" in content and "第二行" in content


def test_summary_line_uses_runsummary_fields():
    # 与 batch.RunSummary 的字段保持一致的守护测试
    summary = RunSummary()
    for field in ("total", "synced", "plain", "not_found", "instrumental",
                  "skipped", "errors", "stopped", "failures"):
        assert hasattr(summary, field)
    assert batch.run_batch is not None

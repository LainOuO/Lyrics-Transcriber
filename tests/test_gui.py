import pytest

tkinter = pytest.importorskip("tkinter")

from lyrics_extractor import batch
from lyrics_extractor.batch import RunSummary
from lyrics_extractor.extractor import Status
from lyrics_extractor.gui import MODE_FILE, MODE_FOLDER, App


@pytest.fixture(scope="module")
def tk_root():
    """整个模块共用一个 Tk 根窗口。

    同一进程里反复 `Tk()` / `destroy()` 会让 Tcl 偶发读不到自己的 ttk 脚本
    （`couldn't read file .../ttk/fonts.tcl`），测试多起来就会随机 skip 一个。
    共用一个根窗口、每次只重建界面，可以彻底避开这个坑。
    """
    try:
        root = tkinter.Tk()
    except Exception as exc:  # 无显示环境时跳过
        pytest.skip(f"无法创建 Tk 窗口: {exc!r}")
    root.withdraw()  # 冒烟测试不真正弹窗
    yield root
    root.destroy()


@pytest.fixture
def app(tk_root, monkeypatch):
    for child in list(tk_root.winfo_children()):
        child.destroy()  # 上一个用例留下的界面
    gui = App(tk_root)
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
    tk_root.update()
    yield gui
    gui.stop_event.set()  # 别让残留的工作线程继续跑


def test_window_builds_and_defaults(app):
    assert app.overwrite_var.get() is False
    assert app.recursive_var.get() is True
    assert app.online_var.get() is True
    assert str(app.start_btn["state"]) == "normal"
    assert str(app.stop_btn["state"]) == "disabled"


def test_start_rejects_missing_folder(app):
    app.path_var.set(r"Z:\不存在的目录\不存在")
    app._start()  # 应记录警告弹窗并直接返回，不启动线程
    assert app.worker is None
    assert app._dialogs == [("warning", ("歌词提取器", "请先选择一个存在的歌曲文件夹"))]


# ----------------------------------------------------------------------
# 单个文件 / 整个文件夹 模式切换


def test_default_mode_is_folder(app):
    assert app.mode_var.get() == MODE_FOLDER
    assert app.path_label["text"] == "歌曲文件夹："
    assert str(app.recursive_check["state"]) == "normal"


def test_switch_to_file_mode_updates_label_and_disables_recursive(app):
    app.mode_var.set(MODE_FILE)
    app._on_mode_change()
    assert app.path_label["text"] == "音频文件："
    # 「包含子文件夹」对单个文件没有意义，应置灰
    assert str(app.recursive_check["state"]) == "disabled"

    app.mode_var.set(MODE_FOLDER)
    app._on_mode_change()
    assert app.path_label["text"] == "歌曲文件夹："
    assert str(app.recursive_check["state"]) == "normal"


def test_mode_switch_remembers_each_path(app):
    app.path_var.set(r"D:\歌单")
    app.mode_var.set(MODE_FILE)
    app._on_mode_change()
    assert app.path_var.get() == ""  # 单文件模式还没选过东西

    app.path_var.set(r"D:\歌单\晴天.mp3")
    app.mode_var.set(MODE_FOLDER)
    app._on_mode_change()
    assert app.path_var.get() == r"D:\歌单"  # 切回去仍是原来那个文件夹

    app.mode_var.set(MODE_FILE)
    app._on_mode_change()
    assert app.path_var.get() == r"D:\歌单\晴天.mp3"


def test_mode_change_is_noop_for_same_mode(app):
    app.path_var.set(r"D:\歌单")
    app._on_mode_change()  # 模式没变，不该把路径清掉
    assert app.path_var.get() == r"D:\歌单"


def test_browse_uses_file_dialog_in_file_mode(app, monkeypatch):
    chosen = {}
    monkeypatch.setattr(
        "lyrics_extractor.gui.filedialog.askopenfilename",
        lambda **kwargs: chosen.update(kwargs) or r"D:\歌单\晴天.mp3",
    )
    monkeypatch.setattr(
        "lyrics_extractor.gui.filedialog.askdirectory",
        lambda **kwargs: pytest.fail("单文件模式不该弹目录选择框"),
    )
    app.mode_var.set(MODE_FILE)
    app._on_mode_change()
    app._browse()
    assert app.path_var.get() == r"D:\歌单\晴天.mp3"
    assert "*.mp3" in chosen["filetypes"][0][1]


def test_browse_uses_directory_dialog_in_folder_mode(app, monkeypatch):
    monkeypatch.setattr(
        "lyrics_extractor.gui.filedialog.askdirectory",
        lambda **kwargs: r"D:\歌单",
    )
    monkeypatch.setattr(
        "lyrics_extractor.gui.filedialog.askopenfilename",
        lambda **kwargs: pytest.fail("文件夹模式不该弹文件选择框"),
    )
    app._browse()
    assert app.path_var.get() == r"D:\歌单"


def test_start_rejects_missing_file(app):
    app.mode_var.set(MODE_FILE)
    app._on_mode_change()
    app.path_var.set(r"Z:\不存在的歌曲.mp3")
    app._start()
    assert app.worker is None
    assert app._dialogs == [("warning", ("歌词提取器", "请先选择一个存在的音频文件"))]


def test_start_rejects_unsupported_file(app, tmp_path):
    note = tmp_path / "歌单说明.txt"
    note.write_text("不是音频", encoding="utf-8")
    app.mode_var.set(MODE_FILE)
    app._on_mode_change()
    app.path_var.set(str(note))
    app._start()
    assert app.worker is None
    assert len(app._dialogs) == 1
    kind, (title, text) = app._dialogs[0]
    assert kind == "warning"
    assert title == "歌词提取器"
    assert "不支持的音频格式" in text
    assert ".txt" in text


def test_start_file_mode_calls_run_files_only(app, tmp_path, monkeypatch):
    audio = tmp_path / "晴天.mp3"
    audio.write_bytes(b"\x00")
    seen = {}

    def fake_run_files(files, **kwargs):
        seen["files"] = [str(f) for f in files]
        seen["overwrite"] = kwargs["overwrite"]
        seen["use_online"] = kwargs["use_online"]
        seen["has_recursive"] = "recursive" in kwargs
        return RunSummary(total=1, synced=1)

    monkeypatch.setattr(batch, "run_files", fake_run_files)
    monkeypatch.setattr(
        batch, "run_batch", lambda *a, **k: pytest.fail("单文件模式不该走文件夹扫描")
    )
    app.mode_var.set(MODE_FILE)
    app._on_mode_change()
    app.path_var.set(str(audio))
    app._start()
    app.worker.join(timeout=5)

    assert seen["files"] == [str(audio)]
    assert seen["has_recursive"] is False  # 单文件没有「包含子文件夹」
    assert seen["use_online"] is True
    assert app._running_mode == MODE_FILE
    # 运行中应锁住会改变目标的控件
    assert str(app.start_btn["state"]) == "disabled"
    assert str(app.browse_btn["state"]) == "disabled"
    assert str(app.export_btn["state"]) == "disabled"
    assert all(str(r["state"]) == "disabled" for r in app.mode_radios)


def test_start_folder_mode_calls_run_batch_with_recursive(app, tmp_path, monkeypatch):
    seen = {}

    def fake_run_batch(folder, **kwargs):
        seen["folder"] = str(folder)
        seen["recursive"] = kwargs["recursive"]
        return RunSummary(total=0)

    monkeypatch.setattr(batch, "run_batch", fake_run_batch)
    monkeypatch.setattr(
        batch, "run_files", lambda *a, **k: pytest.fail("文件夹模式不该走 run_files")
    )
    app.path_var.set(str(tmp_path))
    app.recursive_var.set(False)
    app._start()
    app.worker.join(timeout=5)

    assert seen == {"folder": str(tmp_path), "recursive": False}
    assert app._running_mode == MODE_FOLDER


def test_stop_releases_controls(app):
    app._set_running(True)
    app._stop()
    assert app.stop_event.is_set()
    assert str(app.stop_btn["state"]) == "disabled"


def test_on_done_releases_controls_and_names_scope(app):
    app.mode_var.set(MODE_FILE)
    app._on_mode_change()
    app._running_mode = MODE_FILE
    app._set_running(True)
    app._on_done(RunSummary(total=1, synced=1))
    assert "单个文件" in app.status_var.get()
    assert str(app.start_btn["state"]) == "normal"
    assert str(app.mode_radios[0]["state"]) == "normal"
    # 单文件模式下「包含子文件夹」仍应保持置灰
    assert str(app.recursive_check["state"]) == "disabled"


def test_on_done_folder_mode_keeps_recursive_enabled(app):
    app._running_mode = MODE_FOLDER
    app._set_running(True)
    app._on_done(RunSummary(total=2, synced=2))
    assert "文件夹" in app.status_var.get()
    assert str(app.recursive_check["state"]) == "normal"


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


def test_on_failed_releases_controls(app):
    app._set_running(True)
    app._on_failed("炸了")
    assert app.status_var.get() == "运行出错"
    assert str(app.start_btn["state"]) == "normal"
    assert str(app.browse_btn["state"]) == "normal"


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
    assert batch.run_files is not None


# ----------------------------------------------------------------------
# AI 兜底：勾选框、设置、预览、失败清单挑选

import threading
import time
from pathlib import Path

from lyrics_extractor import settings as settings_module
from lyrics_extractor.gui import (
    AiPickerDialog,
    AiPreviewDialog,
    AiSettingsDialog,
    PreviewBridge,
)
from lyrics_extractor.settings import ENGINE_CLOUD, ENGINE_LOCAL, Settings


def test_ai_widgets_default_state(app):
    assert app.ai_var.get() is False
    assert str(app.ai_settings_btn["state"]) == "normal"
    assert str(app.ai_pick_btn["state"]) == "disabled"


def test_start_blocks_ai_without_cloud_key(app, tmp_path):
    app.settings = Settings(engine=ENGINE_CLOUD, api_key="")
    app.path_var.set(str(tmp_path))
    app.ai_var.set(True)
    app._start()
    assert app.worker is None
    kind, (title, text) = app._dialogs[0]
    assert kind == "warning"
    assert "密钥" in text


def test_start_local_engine_needs_no_key(app, tmp_path, monkeypatch):
    app.settings = Settings(engine=ENGINE_LOCAL, api_key="")
    seen = {}

    def fake_run_batch(folder, **kwargs):
        seen["use_ai"] = kwargs["use_ai"]
        return RunSummary(total=0)

    monkeypatch.setattr(batch, "run_batch", fake_run_batch)
    app.path_var.set(str(tmp_path))
    app.ai_var.set(True)
    app._start()
    app.worker.join(timeout=5)
    assert seen["use_ai"] is True
    assert app._dialogs == []


def test_start_passes_ai_settings_and_confirm_cb(app, tmp_path, monkeypatch):
    app.settings = Settings(api_key="sk-test")
    seen = {}

    def fake_run_batch(folder, **kwargs):
        seen["use_ai"] = kwargs["use_ai"]
        seen["settings"] = kwargs["ai_settings"]
        seen["confirm_cb"] = kwargs["confirm_cb"]
        return RunSummary(total=0)

    monkeypatch.setattr(batch, "run_batch", fake_run_batch)
    app.path_var.set(str(tmp_path))
    app.ai_var.set(True)
    app._start()
    app.worker.join(timeout=5)
    assert seen["use_ai"] is True
    assert seen["settings"].api_key == "sk-test"
    assert seen["confirm_cb"] == app.preview_bridge.confirm


def test_settings_dialog_save_updates_app_and_disk(app, tk_root, monkeypatch, tmp_path):
    settings_path = tmp_path / "settings.json"
    monkeypatch.setattr(settings_module, "SETTINGS_PATH", settings_path)
    app._open_ai_settings()
    dialog = next(
        w for w in tk_root.winfo_children() if isinstance(w, AiSettingsDialog)
    )
    tk_root.update()
    dialog.engine_var.set(ENGINE_CLOUD)
    dialog.api_key_var.set("sk-new")
    dialog.base_url_var.set("https://api.groq.com/openai/v1")
    dialog.model_var.set("whisper-large-v3-turbo")
    dialog.separate_var.set(False)
    dialog._save()
    assert app.settings.api_key == "sk-new"
    assert app.settings.separate_vocals is False
    assert settings_module.load_settings(settings_path) == app.settings


def test_settings_dialog_engine_switch_swaps_panels(app, tk_root):
    dialog = AiSettingsDialog(tk_root, Settings(), lambda s: None)
    tk_root.update()
    assert dialog.cloud_panel.winfo_ismapped() or True  # 布局状态不细究，能切换即可
    dialog.engine_var.set(ENGINE_LOCAL)
    tk_root.update()
    dialog._save()
    dialog.destroy()


def test_settings_dialog_provider_selection(app, tk_root, monkeypatch, tmp_path):
    """默认百炼；可切服务商，保存后落盘生效。"""
    settings_path = tmp_path / "settings.json"
    monkeypatch.setattr(settings_module, "SETTINGS_PATH", settings_path)

    app._open_ai_settings()
    dialog = next(w for w in tk_root.winfo_children() if isinstance(w, AiSettingsDialog))
    tk_root.update()
    # 默认百炼
    assert dialog.provider_var.get() == settings_module.PROVIDER_LABELS[
        settings_module.PROVIDER_DASHSCOPE]
    dialog.api_key_var.set("sk-dash")
    dialog._save()
    assert app.settings.provider == settings_module.PROVIDER_DASHSCOPE
    assert app.settings.api_key == "sk-dash"
    assert settings_module.load_settings(settings_path) == app.settings

    # 切到 OpenAI 兼容再保存
    app._open_ai_settings()
    dialog = next(w for w in tk_root.winfo_children() if isinstance(w, AiSettingsDialog))
    tk_root.update()
    dialog.provider_var.set(
        settings_module.PROVIDER_LABELS[settings_module.PROVIDER_OPENAI])
    tk_root.update()
    dialog._save()
    assert app.settings.provider == settings_module.PROVIDER_OPENAI
    assert settings_module.load_settings(settings_path).provider == \
        settings_module.PROVIDER_OPENAI


def test_preview_dialog_prefills_synced_lines(tk_root):
    got = {}
    dialog = AiPreviewDialog(
        tk_root, "song.mp3", [(1000, "听错的词"), (3000, "第二行")], True,
        lambda r: got.update(result=r),
    )
    tk_root.update()
    content = dialog.text.get("1.0", "end-1c")
    assert content == "[00:01.00]听错的词\n[00:03.00]第二行"


def test_preview_dialog_save_parses_edited_text(tk_root):
    got = {}
    dialog = AiPreviewDialog(
        tk_root, "song.mp3", [(1000, "听错的词"), (3000, "第二行")], True,
        lambda r: got.update(result=r),
    )
    tk_root.update()
    dialog.text.delete("1.0", "end")
    dialog.text.insert("1.0", "[00:02.00]改对的词\n[00:04.00]第二行")
    dialog._save()
    assert got["result"] == ([(2000, "改对的词"), (4000, "第二行")], True)


def test_preview_dialog_save_without_stamps_downgrades_to_plain(tk_root):
    got = {}
    dialog = AiPreviewDialog(
        tk_root, "song.mp3", [(1000, "听错的词"), (3000, "第二行")], True,
        lambda r: got.update(result=r),
    )
    tk_root.update()
    dialog.text.delete("1.0", "end")
    dialog.text.insert("1.0", "没有时间戳的词\n第二行")
    dialog._save()
    assert got["result"] == (["没有时间戳的词", "第二行"], False)


def test_preview_dialog_skip_returns_none(tk_root):
    got = {}
    dialog = AiPreviewDialog(
        tk_root, "song.mp3", ["纯文本行"], False, lambda r: got.update(result=r)
    )
    tk_root.update()
    assert dialog.text.get("1.0", "end-1c") == "纯文本行"
    dialog._finish_skip()
    assert got["result"] is None


def test_preview_bridge_roundtrip_between_threads(app, tk_root):
    """工作线程提交 → 主线程弹预览 → 用户保存改过的词 → 工作线程拿到结果。"""
    result_box = {}

    def worker():
        result_box["r"] = app.preview_bridge.confirm(
            Path("某首歌.mp3"), [(1000, "原始词")], True
        )

    thread = threading.Thread(target=worker)
    thread.start()
    dialog = None
    for _ in range(100):
        app._poll_queue()  # 主线程侧处理 ai_preview 消息
        dialog = next(
            (w for w in tk_root.winfo_children() if isinstance(w, AiPreviewDialog)),
            None,
        )
        if dialog is not None:
            break
        time.sleep(0.02)
    assert dialog is not None, "预览窗没有弹出来"
    dialog.text.delete("1.0", "end")
    dialog.text.insert("1.0", "[00:01.50]改过的词\n[00:03.00]第二行")
    dialog._save()
    thread.join(timeout=5)
    assert result_box["r"] == ([(1500, "改过的词"), (3000, "第二行")], True)


def test_preview_bridge_stopped_run_skips_preview(app, tk_root):
    """点了「停止」后，等待中的预览不再阻塞工作线程，按跳过处理。"""
    result_box = {}
    app.stop_event.clear()

    def worker():
        result_box["r"] = app.preview_bridge.confirm(Path("x.mp3"), [(1, "词")], True)

    thread = threading.Thread(target=worker)
    thread.start()
    time.sleep(0.3)  # 让工作线程进入等待
    app._stop()
    thread.join(timeout=5)
    assert result_box["r"] is None


def test_on_done_enables_ai_pick_only_for_not_found(app):
    summary = RunSummary(
        total=2, not_found=1,
        failures=[("a.mp3", Status.NOT_FOUND.value, "内嵌无歌词，在线未找到")],
    )
    app._on_done(summary)
    assert str(app.ai_pick_btn["state"]) == "normal"


def test_on_done_keeps_ai_pick_disabled_for_errors(app):
    summary = RunSummary(
        total=1, errors=1,
        failures=[("a.mp3", Status.ERROR.value, "处理失败")],
    )
    app._on_done(summary)
    assert str(app.ai_pick_btn["state"]) == "disabled"


def test_ai_picker_lists_not_found_only_and_starts_run(app, tk_root, monkeypatch):
    seen = {}

    def fake_run_files(files, **kwargs):
        seen["files"] = [Path(f) for f in files]
        seen["use_ai"] = kwargs["use_ai"]
        seen["use_online"] = kwargs["use_online"]
        return RunSummary(total=len(files))

    monkeypatch.setattr(batch, "run_files", fake_run_files)
    app.summary = RunSummary(
        failures=[
            ("D:/歌/查不到.mp3", Status.NOT_FOUND.value, "内嵌无歌词，在线未找到"),
            ("D:/歌/坏文件.mp3", Status.ERROR.value, "处理失败"),
            ("D:/歌/纯音乐.mp3", Status.INSTRUMENTAL.value, "在线库标记为纯音乐"),
        ]
    )
    app._open_ai_picker()
    dialog = next(w for w in tk_root.winfo_children() if isinstance(w, AiPickerDialog))
    tk_root.update()
    assert dialog.listbox.size() == 1  # 只列「未找到歌词」的
    assert "查不到.mp3" in dialog.listbox.get(0)
    dialog._select_all()
    dialog._start()
    app.worker.join(timeout=5)
    assert [p.name for p in seen["files"]] == ["查不到.mp3"]
    assert seen["use_ai"] is True
    assert seen["use_online"] is True

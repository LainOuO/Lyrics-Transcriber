"""tkinter 图形界面：选单个文件或整个文件夹、开始提取、进度条、日志、导出失败清单。

AI 兜底：勾选后对查不到歌词的文件调用 AI 听歌转写（先预览改错字再写入），
也可从失败清单里挑歌手动转写；服务商与密钥在「AI 设置」里配置并持久化到本机。
"""

from __future__ import annotations

import functools
import queue
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, scrolledtext, ttk

from lyrics_extractor import APP_NAME, __version__, batch
from lyrics_extractor import settings as settings_module
from lyrics_extractor.audio_file import SUPPORTED_EXTENSIONS, SUPPORTED_EXTENSIONS_TEXT
from lyrics_extractor.extractor import Status
from lyrics_extractor.lrc_writer import format_timestamp, parse_lrc_text

# 提取范围：整个文件夹（默认，保持老习惯）或单个文件
MODE_FOLDER = "folder"
MODE_FILE = "file"
MODE_AI = "ai"  # 从失败清单挑歌做 AI 转写的独立运行
MODE_LABELS = {MODE_FOLDER: "歌曲文件夹：", MODE_FILE: "音频文件："}
MODE_RADIOS = ((MODE_FOLDER, "整个文件夹"), (MODE_FILE, "单个文件"))
RUN_SCOPE_LABELS = {MODE_FOLDER: "文件夹", MODE_FILE: "单个文件", MODE_AI: "AI 转写"}


def audio_filetypes() -> list[tuple[str, str]]:
    """文件选择框的过滤项：受支持扩展名拼成一条通配符串。"""
    patterns = " ".join(f"*{ext}" for ext in sorted(SUPPORTED_EXTENSIONS))
    return [("音频文件", patterns), ("所有文件", "*.*")]


class App:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.queue: queue.Queue = queue.Queue()
        self.worker: threading.Thread | None = None
        self.stop_event = threading.Event()
        self.summary: batch.RunSummary | None = None
        self._mode = MODE_FOLDER
        self._running_mode = MODE_FOLDER  # 本次运行用的模式（状态行文案用）
        self._paths = {MODE_FOLDER: "", MODE_FILE: ""}  # 两种模式各自记住上次的路径
        self.settings = settings_module.load_settings()  # AI 转写服务设置（本机持久化）
        self.preview_bridge = PreviewBridge(self.queue, self.stop_event)

        root.title(f"{APP_NAME} v{__version__}")
        root.geometry("660x500")
        root.minsize(560, 440)
        self._build_widgets()
        root.after(100, self._poll_queue)

    def _build_widgets(self) -> None:
        pad = {"padx": 10, "pady": 6}
        frame = ttk.Frame(self.root)
        frame.pack(fill="both", expand=True)

        row0 = ttk.Frame(frame)
        row0.pack(fill="x", **pad)
        ttk.Label(row0, text="提取范围：").pack(side="left")
        self.mode_var = tk.StringVar(value=MODE_FOLDER)
        self.mode_radios = []
        for value, text in MODE_RADIOS:
            radio = ttk.Radiobutton(
                row0,
                text=text,
                value=value,
                variable=self.mode_var,
                command=self._on_mode_change,
            )
            radio.pack(side="left", padx=(0, 12))
            self.mode_radios.append(radio)

        row1 = ttk.Frame(frame)
        row1.pack(fill="x", **pad)
        self.path_label = ttk.Label(row1, text=MODE_LABELS[MODE_FOLDER])
        self.path_label.pack(side="left")
        self.path_var = tk.StringVar()
        ttk.Entry(row1, textvariable=self.path_var).pack(
            side="left", fill="x", expand=True, padx=6
        )
        self.browse_btn = ttk.Button(row1, text="浏览…", command=self._browse)
        self.browse_btn.pack(side="left")

        hint = ttk.Label(
            frame,
            text=f"支持格式：{SUPPORTED_EXTENSIONS_TEXT}",
            foreground="#888888",
        )
        hint.pack(fill="x", padx=14)

        row2 = ttk.Frame(frame)
        row2.pack(fill="x", padx=10)
        self.overwrite_var = tk.BooleanVar(value=False)
        self.recursive_var = tk.BooleanVar(value=True)
        self.online_var = tk.BooleanVar(value=True)
        self.ai_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(row2, text="覆盖已有 lrc", variable=self.overwrite_var).pack(
            side="left", padx=(0, 12)
        )
        self.recursive_check = ttk.Checkbutton(
            row2, text="包含子文件夹", variable=self.recursive_var
        )
        self.recursive_check.pack(side="left", padx=(0, 12))
        ttk.Checkbutton(row2, text="在线兜底", variable=self.online_var).pack(
            side="left", padx=(0, 12)
        )
        ttk.Checkbutton(row2, text="AI 兜底", variable=self.ai_var).pack(side="left")
        self.ai_settings_btn = ttk.Button(
            row2, text="AI 设置…", command=self._open_ai_settings
        )
        self.ai_settings_btn.pack(side="left", padx=(12, 0))

        row3 = ttk.Frame(frame)
        row3.pack(fill="x", padx=10, pady=6)
        self.start_btn = ttk.Button(row3, text="开始提取", command=self._start)
        self.start_btn.pack(side="left")
        self.stop_btn = ttk.Button(
            row3, text="停止", command=self._stop, state="disabled"
        )
        self.stop_btn.pack(side="left", padx=8)
        self.export_btn = ttk.Button(
            row3, text="导出失败清单", command=self._export, state="disabled"
        )
        self.export_btn.pack(side="right")
        self.ai_pick_btn = ttk.Button(
            row3, text="AI 提取失败项…", command=self._open_ai_picker, state="disabled"
        )
        self.ai_pick_btn.pack(side="right", padx=(0, 8))

        self.progress = ttk.Progressbar(frame, mode="determinate")
        self.progress.pack(fill="x", padx=10, pady=(2, 0))
        self.status_var = tk.StringVar(value="等待开始")
        ttk.Label(frame, textvariable=self.status_var, anchor="w").pack(
            fill="x", padx=10
        )

        self.log_text = scrolledtext.ScrolledText(
            frame, state="disabled", font=("Microsoft YaHei UI", 9)
        )
        self.log_text.pack(fill="both", expand=True, padx=10, pady=8)

    # ------------------------------------------------------------------
    # 按钮动作
    def _on_mode_change(self) -> None:
        """切换「整个文件夹 / 单个文件」：换标签、换记忆的路径、按模式开关无关选项。"""
        new_mode = self.mode_var.get()
        if new_mode == self._mode:
            return
        self._paths[self._mode] = self.path_var.get()  # 存下旧模式填过的路径
        self._mode = new_mode
        self.path_var.set(self._paths[new_mode])  # 换回新模式上次用的路径
        self.path_label.config(text=MODE_LABELS[new_mode])
        # 「包含子文件夹」只对文件夹模式有意义
        self.recursive_check.config(
            state="disabled" if new_mode == MODE_FILE else "normal"
        )

    def _browse(self) -> None:
        if self.mode_var.get() == MODE_FILE:
            chosen = filedialog.askopenfilename(
                title="选择音频文件", filetypes=audio_filetypes()
            )
        else:
            chosen = filedialog.askdirectory(title="选择歌曲文件夹")
        if chosen:
            self.path_var.set(chosen)

    def _start(self) -> None:
        mode = self.mode_var.get()
        raw = self.path_var.get().strip()
        if mode == MODE_FILE:
            path = Path(raw) if raw else None
            if path is None or not path.is_file():
                messagebox.showwarning(APP_NAME, "请先选择一个存在的音频文件")
                return
            if not batch.is_supported_audio(path):
                messagebox.showwarning(
                    APP_NAME,
                    f"不支持的音频格式：{path.suffix or '（没有扩展名）'}\n"
                    f"支持格式：{SUPPORTED_EXTENSIONS_TEXT}",
                )
                return
            # 单文件模式只处理这一个文件，不走文件夹扫描，也没有「子文件夹」这回事
            job = functools.partial(batch.run_files, [path])
        else:
            if not raw or not Path(raw).is_dir():
                messagebox.showwarning(APP_NAME, "请先选择一个存在的歌曲文件夹")
                return
            job = functools.partial(
                batch.run_batch, raw, recursive=self.recursive_var.get()
            )

        if self.ai_var.get() and not self._ai_ready():
            return

        kwargs = self._worker_kwargs(use_ai=self.ai_var.get())
        self._running_mode = mode
        self._launch(job, kwargs, "正在处理…" if mode == MODE_FILE else "正在扫描…")

    def _ai_ready(self) -> bool:
        """勾了「AI 兜底」时的最低可用性检查；不满足时弹提示并返回 False。"""
        if self.settings.engine != settings_module.ENGINE_CLOUD:
            return True  # 本机引擎不需要密钥；依赖缺失会在逐首处理时报清楚的错
        if self.settings.api_key.strip():
            return True
        messagebox.showwarning(
            APP_NAME,
            "已勾选「AI 兜底」，但还没有填写云端服务的密钥。\n\n"
            "请点旁边的「AI 设置…」填写（在服务商网站免费注册即可拿到），"
            "或取消勾选「AI 兜底」。",
        )
        return False

    def _worker_kwargs(self, *, use_ai: bool) -> dict:
        return dict(
            overwrite=self.overwrite_var.get(),
            use_online=self.online_var.get(),
            use_ai=use_ai,
            ai_settings=self.settings,
            confirm_cb=self.preview_bridge.confirm,
            stop_event=self.stop_event,
            progress_cb=lambda done, total, result=None: self.queue.put(
                ("progress", done, total, result)
            ),
            log_cb=lambda msg: self.queue.put(("log", msg)),
        )

    def _launch(self, job, kwargs: dict, status_text: str) -> None:
        self.stop_event.clear()
        self.summary = None
        self._set_running(True)
        self._log_clear()
        self.progress.config(value=0, maximum=100)
        self.status_var.set(status_text)
        self.worker = threading.Thread(
            target=self._run_worker, args=(job, kwargs), daemon=True
        )
        self.worker.start()

    def _set_running(self, running: bool) -> None:
        """运行中锁住「开始」与所有会改变目标的控件，避免中途换目标对不上日志。"""
        self.start_btn.config(state="disabled" if running else "normal")
        self.stop_btn.config(state="normal" if running else "disabled")
        for widget in (self.browse_btn, self.ai_settings_btn, *self.mode_radios):
            widget.config(state="disabled" if running else "normal")
        self.recursive_check.config(
            state="disabled"
            if running or self.mode_var.get() == MODE_FILE
            else "normal"
        )
        # 失败清单挑选也一样：跑新任务时旧清单不再可用，跑完按结果重新启用
        self.ai_pick_btn.config(state="disabled")
        if running:
            # 本次结果还没出来，别让用户导出上一轮的失败清单
            self.export_btn.config(state="disabled")

    def _run_worker(self, job, kwargs: dict) -> None:
        try:
            self.queue.put(("done", job(**kwargs)))
        except Exception as exc:  # 线程里的异常必须送回主线程展示，不能静默消失
            self.queue.put(("failed", str(exc)))

    def _stop(self) -> None:
        self.stop_event.set()
        self.stop_btn.config(state="disabled")

    def _export(self) -> None:
        if not self.summary or not self.summary.failures:
            return
        path = filedialog.asksaveasfilename(
            title="导出失败清单",
            defaultextension=".txt",
            initialfile="失败清单.txt",
            filetypes=[("文本文件", "*.txt")],
        )
        if not path:
            return
        out = batch.export_failure_report(self.summary, path)
        messagebox.showinfo(APP_NAME, f"已导出 {len(self.summary.failures)} 条到：\n{out}")

    # ------------------------------------------------------------------
    # AI 兜底：设置、失败清单挑选、预览

    def _open_ai_settings(self) -> None:
        def on_save(new_settings: settings_module.Settings) -> None:
            self.settings = new_settings
            settings_module.save_settings(new_settings)

        AiSettingsDialog(self.root, self.settings, on_save)

    def _open_ai_picker(self) -> None:
        """失败清单里挑「未找到歌词」的歌交给 AI 转写（其它状态各有原因，不硬转）。"""
        not_found = [
            (path, reason)
            for path, status, reason in (self.summary.failures if self.summary else [])
            if status == Status.NOT_FOUND.value
        ]
        if not not_found:
            return
        AiPickerDialog(self.root, not_found, self._start_ai_for_files)

    def _start_ai_for_files(self, paths) -> None:
        """挑出的歌再跑一遍完整流水线（在线重查 + AI 兜底 + 预览确认）。"""
        self._running_mode = MODE_AI
        kwargs = self._worker_kwargs(use_ai=True)
        kwargs["use_online"] = True  # 上次可能只是没勾在线兜底，先让在线再试一遍
        job = functools.partial(batch.run_files, list(paths))
        self._launch(job, kwargs, "AI 转写中…")

    def _show_ai_preview(self, path, lines, synced, holder, done) -> None:
        def on_done(result) -> None:
            holder["result"] = result
            done.set()

        AiPreviewDialog(self.root, Path(path).name, lines, synced, on_done)

    # ------------------------------------------------------------------
    # 主线程轮询工作线程的消息
    def _poll_queue(self) -> None:
        if not self.log_text.winfo_exists():
            # 控件已被销毁（窗口关了、或测试里换了界面），别再排下一次轮询
            return
        try:
            while True:
                item = self.queue.get_nowait()
                kind = item[0]
                if kind == "log":
                    self._log(item[1])
                elif kind == "progress":
                    _, done, total, _result = item
                    if total:
                        self.progress.config(maximum=total, value=done)
                    self.status_var.set(f"处理中 {done}/{total}")
                elif kind == "done":
                    self._on_done(item[1])
                elif kind == "failed":
                    self._on_failed(item[1])
                elif kind == "ai_preview":
                    # 工作线程的 AI 转写结果弹预览窗；对话框决定后由桥唤醒工作线程
                    _, path, lines, synced, holder, done = item
                    self._show_ai_preview(path, lines, synced, holder, done)
        except queue.Empty:
            pass
        self.root.after(100, self._poll_queue)

    def _on_done(self, summary: batch.RunSummary) -> None:
        self.summary = summary
        self._set_running(False)
        if summary.failures:
            self.export_btn.config(state="normal")
            if any(
                status == Status.NOT_FOUND.value for _, status, _ in summary.failures
            ):
                self.ai_pick_btn.config(state="normal")
        tail = "（已停止）" if summary.stopped else ""
        scope = RUN_SCOPE_LABELS.get(self._running_mode, "文件夹")
        self.status_var.set(
            f"完成{tail}（{scope}）：共 {summary.total} 个，写入 {summary.written}"
            f"（同步 {summary.synced} / 纯文本 {summary.plain}），"
            f"未找到 {summary.not_found}，纯音乐 {summary.instrumental}，"
            f"跳过 {summary.skipped}，失败 {summary.errors}"
        )

    def _on_failed(self, message: str) -> None:
        self._set_running(False)
        self.status_var.set("运行出错")
        messagebox.showerror(APP_NAME, f"批量处理时发生异常：\n{message}")

    # ------------------------------------------------------------------
    def _log(self, msg: str) -> None:
        self.log_text.config(state="normal")
        self.log_text.insert("end", msg + "\n")
        self.log_text.see("end")
        self.log_text.config(state="disabled")

    def _log_clear(self) -> None:
        self.log_text.config(state="normal")
        self.log_text.delete("1.0", "end")
        self.log_text.config(state="disabled")


# ----------------------------------------------------------------------
# AI 兜底相关的桥与对话框


class PreviewBridge:
    """工作线程与 tkinter 主线程之间的预览桥。

    工作线程提交转写结果并阻塞等待；主线程弹预览窗，用户改完词或跳过后
    唤醒工作线程继续。用户点「停止」时不再等待，剩余预览按跳过处理。
    """

    def __init__(self, queue, stop_event):
        self.queue = queue
        self.stop_event = stop_event

    def confirm(self, path, lines, synced):
        holder = {}
        done = threading.Event()
        self.queue.put(("ai_preview", path, lines, synced, holder, done))
        while not done.wait(0.2):
            if self.stop_event.is_set():
                return None
        return holder.get("result")


class ModalDialog(tk.Toplevel):
    """带防御性模态抓取的对话框基类（窗口尚未可见时跳过抓取，便于自动化测试）。"""

    def _try_grab(self) -> None:
        try:
            self.grab_set()
        except tk.TclError:
            pass


class AiPreviewDialog(ModalDialog):
    """AI 转写结果预览：可改错字再保存，也可跳过不写。

    带时间戳的行保存后仍是同步歌词；把时间戳删掉再保存则降级为纯文本歌词。
    """

    def __init__(self, master, filename: str, lines, synced: bool, on_done):
        super().__init__(master)
        self.title(f"AI 转写结果 —— {filename}")
        self.on_done = on_done

        ttk.Label(
            self,
            text="检查下面的歌词，改完错字点「保存 .lrc」。\n"
                 "行首的 [分:秒.毫秒] 就是这一行的开始时间，保留它就是带时间轴的歌词，"
                 "全部删掉则保存为纯文本。",
            foreground="#666666",
        ).pack(fill="x", padx=10, pady=(8, 4))

        self.text = scrolledtext.ScrolledText(
            self, width=64, height=18, font=("Microsoft YaHei UI", 10)
        )
        self.text.pack(fill="both", expand=True, padx=10, pady=4)
        if synced:
            content = "\n".join(f"[{format_timestamp(ms)}]{body}" for ms, body in lines)
        else:
            content = "\n".join(str(line) for line in lines)
        self.text.insert("1.0", content)

        row = ttk.Frame(self)
        row.pack(fill="x", padx=10, pady=(4, 10))
        ttk.Button(row, text="保存 .lrc", command=self._save).pack(side="right")
        ttk.Button(row, text="跳过这首", command=self._finish_skip).pack(
            side="right", padx=(0, 8)
        )

        self.protocol("WM_DELETE_WINDOW", self._finish_skip)
        self.transient(master)
        self._try_grab()

    def _save(self) -> None:
        text = self.text.get("1.0", "end-1c")
        parsed = parse_lrc_text(text)
        if parsed:
            self._finish((parsed, True))
            return
        plain = [line.strip() for line in text.splitlines() if line.strip()]
        self._finish((plain, False))

    def _finish_skip(self) -> None:
        self._finish(None)

    def _finish(self, result) -> None:
        callback = self.on_done
        self.on_done = None  # 关窗路径与按钮路径只会走到一个，防重复回调
        self.destroy()
        if callback is not None:
            callback(result)


class AiSettingsDialog(ModalDialog):
    """AI 转写服务设置：引擎、云端三件套（地址/模型/密钥）、本地模型、人声分离。"""

    LOCAL_MODELS = ("tiny", "base", "small", "medium", "large-v3")

    def __init__(self, master, current: settings_module.Settings, on_save):
        super().__init__(master)
        self.title("AI 设置")
        self.on_save = on_save
        self.resizable(False, False)

        body = ttk.Frame(self)
        body.pack(fill="both", expand=True, padx=12, pady=10)

        self.engine_var = tk.StringVar(value=current.engine)
        self.engine_var.trace_add("write", lambda *_: self._refresh_panels())
        engine_row = ttk.Frame(body)
        engine_row.pack(fill="x", pady=(0, 6))
        ttk.Label(engine_row, text="转写引擎：").pack(side="left")
        for value, text in settings_module.ENGINE_LABELS.items():
            ttk.Radiobutton(
                engine_row, text=text, value=value, variable=self.engine_var
            ).pack(side="left", padx=(0, 10))

        self.cloud_panel = ttk.LabelFrame(body, text="云端接口（OpenAI 兼容）")
        self.base_url_var = tk.StringVar(value=current.base_url)
        self.model_var = tk.StringVar(value=current.model)
        self.api_key_var = tk.StringVar(value=current.api_key)
        self._entry_row(self.cloud_panel, "接口地址：", self.base_url_var)
        self._entry_row(self.cloud_panel, "模型名：", self.model_var)
        self._entry_row(self.cloud_panel, "密钥：", self.api_key_var, show="*")
        ttk.Label(
            self.cloud_panel,
            text="默认 Groq：在 console.groq.com 免费注册即可拿到密钥，转写有免费额度。\n"
                 "任何 OpenAI 兼容的转写服务都能用，改地址、模型名和密钥即可。",
            foreground="#888888",
        ).pack(fill="x", padx=8, pady=(0, 6))

        self.local_panel = ttk.LabelFrame(body, text="本机模型（离线，需装增强包）")
        self.local_model_var = tk.StringVar(value=current.local_model)
        row = ttk.Frame(self.local_panel)
        row.pack(fill="x", padx=8, pady=(6, 2))
        ttk.Label(row, text="Whisper 型号：").pack(side="left")
        ttk.Combobox(
            row, textvariable=self.local_model_var,
            values=self.LOCAL_MODELS, state="readonly", width=12,
        ).pack(side="left")
        ttk.Label(
            self.local_panel,
            text="需先安装增强包：命令行执行 pip install whisper-timestamped，"
                 "并确保 ffmpeg 可用（详见 README）。",
            foreground="#888888",
        ).pack(fill="x", padx=8, pady=(2, 6))

        self.separate_var = tk.BooleanVar(value=current.separate_vocals)
        ttk.Checkbutton(
            body,
            text="人声分离增强（先剥离伴奏再转写，更准但更慢；需装 Demucs 增强包，未装自动跳过）",
            variable=self.separate_var,
        ).pack(anchor="w", pady=(6, 2))

        btns = ttk.Frame(body)
        btns.pack(fill="x", pady=(10, 0))
        ttk.Button(btns, text="保存", command=self._save).pack(side="right")
        ttk.Button(btns, text="取消", command=self.destroy).pack(side="right", padx=(0, 8))

        self.protocol("WM_DELETE_WINDOW", self.destroy)
        self.transient(master)
        self._refresh_panels()
        self._try_grab()

    def _entry_row(self, parent, label: str, var, show=None) -> None:
        row = ttk.Frame(parent)
        row.pack(fill="x", padx=8, pady=2)
        ttk.Label(row, text=label, width=10).pack(side="left")
        ttk.Entry(row, textvariable=var, show=show).pack(
            side="left", fill="x", expand=True
        )

    def _refresh_panels(self) -> None:
        """按引擎切换云端/本地面板。"""
        if self.engine_var.get() == settings_module.ENGINE_LOCAL:
            self.cloud_panel.pack_forget()
            self.local_panel.pack(fill="x", pady=4)
        else:
            self.local_panel.pack_forget()
            self.cloud_panel.pack(fill="x", pady=4)

    def _save(self) -> None:
        settings = settings_module.Settings(
            engine=self.engine_var.get(),
            base_url=self.base_url_var.get().strip() or settings_module.DEFAULT_BASE_URL,
            api_key=self.api_key_var.get().strip(),
            model=self.model_var.get().strip() or settings_module.DEFAULT_MODEL,
            local_model=self.local_model_var.get() or settings_module.DEFAULT_LOCAL_MODEL,
            separate_vocals=self.separate_var.get(),
        )
        self.destroy()
        self.on_save(settings)


class AiPickerDialog(ModalDialog):
    """失败清单挑选：选「未找到歌词」的歌交给 AI 转写。"""

    def __init__(self, master, failures, on_start):
        super().__init__(master)
        self.title("AI 提取失败项")
        self.on_start = on_start

        ttk.Label(
            self,
            text=f"共 {len(failures)} 首没找到歌词。选中要交给 AI 转写的歌"
                 f"（Ctrl/Shift 多选，Ctrl+A 全选），点「开始 AI 转写」。",
            foreground="#666666",
        ).pack(fill="x", padx=10, pady=(8, 4))

        self.listbox = tk.Listbox(self, selectmode="extended", width=72, height=16)
        self.listbox.pack(fill="both", expand=True, padx=10, pady=4)
        for path, _reason in failures:
            self.listbox.insert("end", path)
        self.listbox.bind("<Control-a>", self._select_all)

        row = ttk.Frame(self)
        row.pack(fill="x", padx=10, pady=(4, 10))
        ttk.Button(row, text="开始 AI 转写", command=self._start).pack(side="right")
        ttk.Button(row, text="全选", command=self._select_all).pack(side="right", padx=(0, 8))
        ttk.Button(row, text="取消", command=self.destroy).pack(side="right", padx=(0, 8))

        self.transient(master)
        self._try_grab()

    def _select_all(self, event=None):
        self.listbox.selection_set(0, "end")
        return "break"

    def _start(self) -> None:
        selection = self.listbox.curselection()
        if not selection:
            return
        paths = [Path(self.listbox.get(index)) for index in selection]
        callback = self.on_start
        self.on_start = None
        self.destroy()
        if callback is not None:
            callback(paths)


def main() -> None:
    root = tk.Tk()
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()

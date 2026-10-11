"""tkinter 图形界面：选单个文件或整个文件夹、开始提取、进度条、日志、导出失败清单。"""

from __future__ import annotations

import functools
import queue
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, scrolledtext, ttk

from lyrics_extractor import APP_NAME, __version__, batch
from lyrics_extractor.audio_file import SUPPORTED_EXTENSIONS, SUPPORTED_EXTENSIONS_TEXT

# 提取范围：整个文件夹（默认，保持老习惯）或单个文件
MODE_FOLDER = "folder"
MODE_FILE = "file"
MODE_LABELS = {MODE_FOLDER: "歌曲文件夹：", MODE_FILE: "音频文件："}
MODE_RADIOS = ((MODE_FOLDER, "整个文件夹"), (MODE_FILE, "单个文件"))


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
        ttk.Checkbutton(row2, text="覆盖已有 lrc", variable=self.overwrite_var).pack(
            side="left", padx=(0, 12)
        )
        self.recursive_check = ttk.Checkbutton(
            row2, text="包含子文件夹", variable=self.recursive_var
        )
        self.recursive_check.pack(side="left", padx=(0, 12))
        ttk.Checkbutton(row2, text="在线兜底", variable=self.online_var).pack(side="left")

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

        self.stop_event.clear()
        self.summary = None
        self._running_mode = mode
        self._set_running(True)
        self._log_clear()
        self.progress.config(value=0, maximum=100)
        self.status_var.set("正在处理…" if mode == MODE_FILE else "正在扫描…")
        kwargs = dict(
            overwrite=self.overwrite_var.get(),
            use_online=self.online_var.get(),
            stop_event=self.stop_event,
            progress_cb=lambda done, total, result=None: self.queue.put(
                ("progress", done, total, result)
            ),
            log_cb=lambda msg: self.queue.put(("log", msg)),
        )
        self.worker = threading.Thread(
            target=self._run_worker, args=(job, kwargs), daemon=True
        )
        self.worker.start()

    def _set_running(self, running: bool) -> None:
        """运行中锁住「开始」与所有会改变目标的控件，避免中途换目标对不上日志。"""
        self.start_btn.config(state="disabled" if running else "normal")
        self.stop_btn.config(state="normal" if running else "disabled")
        for widget in (self.browse_btn, *self.mode_radios):
            widget.config(state="disabled" if running else "normal")
        self.recursive_check.config(
            state="disabled"
            if running or self.mode_var.get() == MODE_FILE
            else "normal"
        )
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
        except queue.Empty:
            pass
        self.root.after(100, self._poll_queue)

    def _on_done(self, summary: batch.RunSummary) -> None:
        self.summary = summary
        self._set_running(False)
        if summary.failures:
            self.export_btn.config(state="normal")
        tail = "（已停止）" if summary.stopped else ""
        scope = "单个文件" if self._running_mode == MODE_FILE else "文件夹"
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


def main() -> None:
    root = tk.Tk()
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()

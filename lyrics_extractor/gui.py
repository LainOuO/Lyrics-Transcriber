"""tkinter 图形界面：选文件夹、开始提取、进度条、日志、导出失败清单。"""

from __future__ import annotations

import queue
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, scrolledtext, ttk

from lyrics_extractor import APP_NAME, __version__, batch
from lyrics_extractor.audio_file import SUPPORTED_EXTENSIONS_TEXT


class App:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.queue: queue.Queue = queue.Queue()
        self.worker: threading.Thread | None = None
        self.stop_event = threading.Event()
        self.summary: batch.RunSummary | None = None

        root.title(f"{APP_NAME} v{__version__}")
        root.geometry("660x480")
        root.minsize(560, 420)
        self._build_widgets()
        root.after(100, self._poll_queue)

    def _build_widgets(self) -> None:
        pad = {"padx": 10, "pady": 6}
        frame = ttk.Frame(self.root)
        frame.pack(fill="both", expand=True)

        row1 = ttk.Frame(frame)
        row1.pack(fill="x", **pad)
        ttk.Label(row1, text="歌曲文件夹：").pack(side="left")
        self.folder_var = tk.StringVar()
        ttk.Entry(row1, textvariable=self.folder_var).pack(
            side="left", fill="x", expand=True, padx=6
        )
        ttk.Button(row1, text="浏览…", command=self._browse).pack(side="left")

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
        ttk.Checkbutton(row2, text="包含子文件夹", variable=self.recursive_var).pack(
            side="left", padx=(0, 12)
        )
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
    def _browse(self) -> None:
        folder = filedialog.askdirectory(title="选择歌曲文件夹")
        if folder:
            self.folder_var.set(folder)

    def _start(self) -> None:
        folder = self.folder_var.get().strip()
        if not folder or not Path(folder).is_dir():
            messagebox.showwarning(APP_NAME, "请先选择一个存在的歌曲文件夹")
            return
        self.stop_event.clear()
        self.summary = None
        self.export_btn.config(state="disabled")
        self.start_btn.config(state="disabled")
        self.stop_btn.config(state="normal")
        self._log_clear()
        self.progress.config(value=0, maximum=100)
        self.status_var.set("正在扫描…")
        args = dict(
            folder=folder,
            overwrite=self.overwrite_var.get(),
            recursive=self.recursive_var.get(),
            use_online=self.online_var.get(),
            stop_event=self.stop_event,
            progress_cb=lambda done, total, result=None: self.queue.put(
                ("progress", done, total, result)
            ),
            log_cb=lambda msg: self.queue.put(("log", msg)),
        )
        self.worker = threading.Thread(
            target=self._run_batch, args=(args,), daemon=True
        )
        self.worker.start()

    def _run_batch(self, args: dict) -> None:
        try:
            summary = batch.run_batch(**args)
            self.queue.put(("done", summary))
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
        self.start_btn.config(state="normal")
        self.stop_btn.config(state="disabled")
        if summary.failures:
            self.export_btn.config(state="normal")
        tail = "（已停止）" if summary.stopped else ""
        self.status_var.set(
            f"完成{tail}：共 {summary.total} 个，写入 {summary.written}"
            f"（同步 {summary.synced} / 纯文本 {summary.plain}），"
            f"未找到 {summary.not_found}，纯音乐 {summary.instrumental}，"
            f"跳过 {summary.skipped}，失败 {summary.errors}"
        )

    def _on_failed(self, message: str) -> None:
        self.start_btn.config(state="normal")
        self.stop_btn.config(state="disabled")
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

"""文件夹批量调度、汇总统计与失败清单导出。"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from pathlib import Path

from lyrics_extractor.audio_file import SUPPORTED_EXTENSIONS
from lyrics_extractor.extractor import LyricsExtractor, Status


def scan_audio_files(folder, recursive: bool = True) -> list[Path]:
    """扫描文件夹下所有受支持的音频文件（按扩展名集合，大小写不敏感）。"""
    folder = Path(folder)
    if not folder.is_dir():
        return []
    iterator = folder.rglob("*") if recursive else folder.glob("*")
    return sorted(
        path
        for path in iterator
        if path.is_file() and path.suffix.lower() in SUPPORTED_EXTENSIONS
    )


# 兼容旧名（历史上只支持 mp3）；新代码请用 scan_audio_files
scan_mp3s = scan_audio_files


@dataclass
class RunSummary:
    total: int = 0
    synced: int = 0
    plain: int = 0
    not_found: int = 0
    instrumental: int = 0
    skipped: int = 0
    errors: int = 0
    stopped: bool = False
    failures: list = field(default_factory=list)  # [(路径, 状态文本, 原因)]

    @property
    def written(self) -> int:
        return self.synced + self.plain


def run_batch(
    folder,
    *,
    overwrite: bool = False,
    recursive: bool = True,
    use_online: bool = True,
    stop_event: threading.Event | None = None,
    progress_cb=None,
    log_cb=None,
    timeout: float | None = None,
) -> RunSummary:
    """遍历文件夹逐个处理；通过回调向界面汇报进度与日志，stop_event 置位时提前停止。"""
    log = log_cb or (lambda msg: None)
    progress = progress_cb or (lambda done, total, result=None: None)
    files = scan_audio_files(folder, recursive=recursive)
    summary = RunSummary(total=len(files))
    if not files:
        log("该文件夹下没有找到支持的音频文件")
        return summary

    kwargs = {"overwrite": overwrite, "use_online": use_online}
    if timeout is not None:
        kwargs["timeout"] = timeout
    extractor = LyricsExtractor(**kwargs)

    counters = {
        Status.SYNCED: "synced",
        Status.PLAIN: "plain",
        Status.NOT_FOUND: "not_found",
        Status.INSTRUMENTAL: "instrumental",
        Status.SKIPPED: "skipped",
        Status.ERROR: "errors",
    }

    for index, file_path in enumerate(files, 1):
        if stop_event is not None and stop_event.is_set():
            summary.stopped = True
            log("已按要求停止")
            break
        result = extractor.process(file_path)
        setattr(summary, counters[result.status], getattr(summary, counters[result.status]) + 1)
        if result.status in (Status.NOT_FOUND, Status.INSTRUMENTAL, Status.ERROR):
            summary.failures.append((str(result.path), result.status.value, result.message))
        detail = " ".join(x for x in (result.source, result.message) if x)
        log(f"[{index}/{summary.total}] {file_path.name} —— {result.status.value}"
            + (f"（{detail}）" if detail else ""))
        progress(index, summary.total, result)

    return summary


def export_failure_report(summary: RunSummary, out_path) -> Path:
    """把失败/纯音乐/出错清单写成 UTF-8（带 BOM）的 txt，方便记事本直接打开。"""
    out = Path(out_path)
    lines = [f"{path}\t{status}\t{reason}" for path, status, reason in summary.failures]
    out.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8-sig")
    return out

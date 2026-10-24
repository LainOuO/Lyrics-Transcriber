"""文件夹批量调度、汇总统计与失败清单导出。"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from pathlib import Path

from lyrics_transcriber.audio_file import SUPPORTED_EXTENSIONS
from lyrics_transcriber.extractor import LyricsTranscriber, Status


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


def is_supported_audio(path) -> bool:
    """该路径是否是一个受支持的音频文件（存在、是文件、扩展名在支持集合内）。

    界面选「单个文件」时用它把关：选到 .txt 或没有扩展名的文件应当直接拦下，
    而不是静默跳过。
    """
    path = Path(path)
    return path.is_file() and path.suffix.lower() in SUPPORTED_EXTENSIONS


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


def run_files(
    files,
    *,
    overwrite: bool = False,
    use_online: bool = True,
    use_ai: bool = False,
    ai_settings=None,
    confirm_cb=None,
    stop_event: threading.Event | None = None,
    progress_cb=None,
    log_cb=None,
    timeout: float | None = None,
) -> RunSummary:
    """逐个处理给定的一批文件（界面的「单个文件」模式就是只传一个）。

    只认传进来的这份列表，不做扩展名过滤也不做排序——过滤交给
    :func:`scan_audio_files` / :func:`is_supported_audio`，调用方先筛好再进来。
    """
    log = log_cb or (lambda msg: None)
    progress = progress_cb or (lambda done, total, result=None: None)
    files = [Path(path) for path in files]
    summary = RunSummary(total=len(files))
    if not files:
        return summary

    kwargs = {
        "overwrite": overwrite,
        "use_online": use_online,
        "use_ai": use_ai,
        "ai_settings": ai_settings,
        "confirm_cb": confirm_cb,
    }
    if timeout is not None:
        kwargs["timeout"] = timeout
    extractor = LyricsTranscriber(**kwargs)

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


def run_batch(
    folder,
    *,
    overwrite: bool = False,
    recursive: bool = True,
    use_online: bool = True,
    use_ai: bool = False,
    ai_settings=None,
    confirm_cb=None,
    stop_event: threading.Event | None = None,
    progress_cb=None,
    log_cb=None,
    timeout: float | None = None,
) -> RunSummary:
    """扫描文件夹后逐个处理；通过回调向界面汇报进度与日志，stop_event 置位时提前停止。"""
    log = log_cb or (lambda msg: None)
    files = scan_audio_files(folder, recursive=recursive)
    if not files:
        log("该文件夹下没有找到支持的音频文件")
        return RunSummary(total=0)
    return run_files(
        files,
        overwrite=overwrite,
        use_online=use_online,
        use_ai=use_ai,
        ai_settings=ai_settings,
        confirm_cb=confirm_cb,
        stop_event=stop_event,
        progress_cb=progress_cb,
        log_cb=log_cb,
        timeout=timeout,
    )


def export_failure_report(summary: RunSummary, out_path) -> Path:
    """把失败/纯音乐/出错清单写成 UTF-8（带 BOM）的 txt，方便记事本直接打开。"""
    out = Path(out_path)
    lines = [f"{path}\t{status}\t{reason}" for path, status, reason in summary.failures]
    out.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8-sig")
    return out

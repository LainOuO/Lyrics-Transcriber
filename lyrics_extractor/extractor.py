"""单文件处理流水线：读内嵌歌词 → 在线兜底 → 写 LRC。

格式无关：通过 :mod:`lyrics_extractor.audio_file` 统一读取标签与内嵌歌词，
支持 mp3 / flac / wav / m4a / mp4 / aac / ogg / opus / wma / ape / aiff。
"""

from __future__ import annotations

import enum
from dataclasses import dataclass
from pathlib import Path

from lyrics_extractor import audio_file, lrc_writer, online


class Status(enum.Enum):
    SYNCED = "同步歌词"
    PLAIN = "纯文本歌词"
    NOT_FOUND = "未找到歌词"
    INSTRUMENTAL = "纯音乐"
    SKIPPED = "已存在跳过"
    ERROR = "处理失败"


@dataclass
class ExtractResult:
    path: Path
    status: Status
    source: str = ""  # 内嵌SYLT / 内嵌USLT / 内嵌LYRICS / 内嵌MP4 / lrclib ...
    message: str = ""

    @property
    def mp3_path(self) -> Path:
        """兼容旧字段名（历史遗留，等价于 path）。"""
        return self.path

    @property
    def ok(self) -> bool:
        return self.status in (Status.SYNCED, Status.PLAIN)


class LyricsExtractor:
    def __init__(
        self,
        overwrite: bool = False,
        use_online: bool = True,
        timeout: float = online.DEFAULT_TIMEOUT,
    ):
        self.overwrite = overwrite
        self.use_online = use_online
        self.timeout = timeout

    def process(self, mp3_path) -> ExtractResult:
        path = Path(mp3_path)
        lrc_path = lrc_writer.lrc_path_for(path)
        if lrc_path.exists() and not self.overwrite:
            return ExtractResult(path, Status.SKIPPED)
        try:
            audio = audio_file.load_audio(path)
        except audio_file.AudioError as exc:
            return ExtractResult(path, Status.ERROR, message=str(exc))

        tags = audio.tags
        title, artist = self._display_tags(path, tags)

        embedded = audio.embedded
        if embedded is not None:
            return self._write(
                path, tags, title, artist, embedded.lines, embedded.synced,
                f"内嵌{embedded.source}",
            )

        if self.use_online and title:
            return self._online(path, tags, title, artist)
        if not self.use_online:
            return ExtractResult(path, Status.NOT_FOUND, message="内嵌无歌词（在线兜底已关闭）")
        return ExtractResult(path, Status.NOT_FOUND, message="内嵌无歌词，且缺少标题无法在线查询")

    # ------------------------------------------------------------------

    def _online(self, path: Path, tags, title: str, artist: str) -> ExtractResult:
        try:
            found = online.fetch_lyrics(
                title,
                artist,
                album=tags.album,
                duration=tags.duration_seconds or None,
                timeout=self.timeout,
            )
        except online.OnlineError as exc:
            return ExtractResult(path, Status.ERROR, message=f"在线查询失败: {exc}")
        if found is None:
            return ExtractResult(path, Status.NOT_FOUND, message="内嵌无歌词，在线未找到")
        if found.instrumental:
            return ExtractResult(path, Status.INSTRUMENTAL, message="在线库标记为纯音乐")

        if found.synced_text:
            lines = lrc_writer.parse_lrc_text(found.synced_text)
            if lines:
                return self._write(path, tags, title, artist, lines, True, "lrclib")
        if found.plain_text:
            plain = [ln.strip() for ln in found.plain_text.splitlines() if ln.strip()]
            if plain:
                return self._write(path, tags, title, artist, plain, False, "lrclib")
        return ExtractResult(path, Status.NOT_FOUND, message="在线接口返回内容为空")

    def _write(self, path, tags, title, artist, lines, synced: bool, source: str) -> ExtractResult:
        lrc_writer.write_lrc(path, title, artist, tags.album, lines, synced)
        message = "" if synced else "来源无时间轴，已降级为纯文本"
        return ExtractResult(path, Status.SYNCED if synced else Status.PLAIN,
                             source=source, message=message)

    @staticmethod
    def _display_tags(path: Path, tags) -> tuple[str, str]:
        """标题为空时回退：按 "歌手 - 标题.<扩展名>" 的命名习惯从文件名补齐。"""
        title, artist = tags.title, tags.artist
        if not title:
            stem = path.stem
            if " - " in stem:
                maybe_artist, maybe_title = stem.split(" - ", 1)
                artist = artist or maybe_artist.strip()
                title = maybe_title.strip()
            else:
                title = stem
        return title, artist

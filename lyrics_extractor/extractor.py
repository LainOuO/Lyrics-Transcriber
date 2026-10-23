"""单文件处理流水线：读内嵌歌词 → 在线兜底 → AI 听歌转写 → 写 LRC。

格式无关：通过 :mod:`lyrics_extractor.audio_file` 统一读取标签与内嵌歌词，
支持 mp3 / flac / wav / m4a / mp4 / aac / ogg / opus / wma / ape / aiff。
"""

from __future__ import annotations

import enum
import re
from dataclasses import dataclass
from pathlib import Path

from lyrics_extractor import (
    ai_transcribe,
    audio_file,
    lrc_writer,
    online,
    settings as app_settings,
)


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
        use_ai: bool = False,
        ai_settings=None,
        confirm_cb=None,
    ):
        self.overwrite = overwrite
        self.use_online = use_online
        self.timeout = timeout
        self.use_ai = use_ai
        self.ai_settings = ai_settings
        # 预览回调：confirm_cb(path, lines, synced) -> (行列表, 是否同步) | None。
        # None 表示用户在预览里跳过；不传则视为直接采纳（批量/无界面场景）。
        self.confirm_cb = confirm_cb

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

        result: ExtractResult | None = None
        if self.use_online and title:
            result = self._online(path, tags, title, artist)

        # AI 兜底只接「没查到」的盘：在线报错（网络问题）或纯音乐不是它的活
        if result is None or result.status == Status.NOT_FOUND:
            if self.use_ai:
                ai_result = self._ai_transcribe(path, tags, title, artist)
                if ai_result is not None:
                    return ai_result
        if result is not None:
            return result
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

    def _ai_transcribe(self, path, tags, title, artist) -> ExtractResult | None:
        """AI 听歌转写兜底；返回 None 表示没转出可用结果（沿用原有 NOT_FOUND）。"""
        try:
            transcription = ai_transcribe.transcribe_audio(
                path, self.ai_settings or app_settings.Settings()
            )
        except ai_transcribe.TranscribeError as exc:
            return ExtractResult(path, Status.ERROR, message=f"AI 转写失败: {exc}")
        if transcription.empty:
            return ExtractResult(
                path, Status.NOT_FOUND, source="AI 转写",
                message="AI 未识别出人声歌词（可能是纯音乐）",
            )

        if transcription.lines:
            lines, synced = transcription.lines, True
        else:
            lines, synced = transcription.plain_lines, False

        if self.confirm_cb is not None:
            edited = self.confirm_cb(path, lines, synced)
            if edited is None or not edited[0]:
                return ExtractResult(
                    path, Status.NOT_FOUND, source="AI 转写",
                    message="AI 转写完成，但已在预览中跳过、未保存",
                )
            lines, synced = edited
        return self._write(path, tags, title, artist, lines, synced, "AI 转写")

    def _write(self, path, tags, title, artist, lines, synced: bool, source: str) -> ExtractResult:
        lrc_writer.write_lrc(path, title, artist, tags.album, lines, synced)
        message = "" if synced else "来源无时间轴，已降级为纯文本"
        return ExtractResult(path, Status.SYNCED if synced else Status.PLAIN,
                             source=source, message=message)

    # 文件名回退时剥掉开头的轨道号，如 "01." / "02 -" / "1_"
    _TRACK_PREFIX = re.compile(r"^\d{1,3}\s*[.\-_]\s+")

    @classmethod
    def _display_tags(cls, path: Path, tags) -> tuple[str, str]:
        """标题为空时回退：按 "歌手 - 标题.<扩展名>" 的命名习惯从文件名补齐。

        兼顾常见命名习惯：开头的轨道号（"01. 周杰伦 - 晴天"）剥掉；
        纯数字的前导段（"01 - 周杰伦 - 晴天"）不当成歌手。
        """
        title, artist = tags.title, tags.artist
        if not title:
            stem = cls._TRACK_PREFIX.sub("", path.stem).strip()
            if " - " in stem:
                parts = [part.strip() for part in stem.split(" - ") if part.strip()]
                while len(parts) >= 2 and parts[0].isdigit():
                    parts.pop(0)
                if len(parts) >= 2:
                    artist = artist or parts[0]
                    title = " - ".join(parts[1:])
                elif parts:
                    title = parts[0]
            else:
                title = stem
        return title, artist

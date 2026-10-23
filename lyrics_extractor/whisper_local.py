"""本地转写引擎（whisper-timestamped，可选依赖）：离线可用、词级时间戳。

依赖 ``whisper-timestamped``（连同 torch）与本机 PATH 里的 ffmpeg；未安装时
给出明确的安装指引而不是一串 ImportError。模型首次使用时自动下载并缓存到
用户目录，之后离线可用。
"""

from __future__ import annotations

from pathlib import Path

from lyrics_extractor.ai_transcribe import Transcription, TranscribeError, build_lines
from lyrics_extractor.settings import Settings

# 同一型号只加载一次；批量转写时省去每个文件反复加载模型的等待
_MODELS: dict[str, object] = {}


def transcribe_local(path, settings: Settings) -> Transcription:
    """用本机 whisper 模型转写；任何环境问题都转成带指引的 TranscribeError。"""
    try:
        import whisper_timestamped
    except Exception as exc:
        raise TranscribeError(
            "本机转写引擎未安装：请先在命令行执行 "
            "pip install whisper-timestamped，并确认 ffmpeg 可用"
            "（详见 README 的「AI 增强包」一节）"
        ) from exc

    name = str(getattr(settings, "local_model", "") or "small")
    try:
        model = _MODELS.get(name)
        if model is None:
            model = whisper_timestamped.load_model(name)
            _MODELS[name] = model
        result = whisper_timestamped.transcribe(model, str(Path(path)))
    except Exception as exc:  # ffmpeg 缺失、音频解码失败、内存不足等
        raise TranscribeError(f"本机转写失败: {exc}") from exc

    words = [
        word
        for segment in result.get("segments", [])
        for word in segment.get("words", [])
    ]
    return build_lines(words=words, text=result.get("text") or "")

"""本地转写引擎（whisper-timestamped，可选依赖）：离线可用、逐字时间戳。

依赖 ``whisper-timestamped``（连同 torch）。读音频要 ffmpeg：PATH 里有就用，
没有就自动借用 ``imageio_ffmpeg`` 包自带的静态版（pip 装增强包时顺带装上即可，
不必再单独安装系统 ffmpeg）。未安装时给出明确的安装指引而不是一串
ImportError。模型首次使用时自动下载并缓存到用户目录，之后离线可用。

实测注意（2026-10，本机 CPU）：whisper-timestamped 1.15.9 的词字段是 ``text``
而不是 ``word``（每词 word=None），下面取词时两个字段名都认。
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

from lyrics_transcriber.ai_transcribe import Transcription, TranscribeError, build_lines
from lyrics_transcriber.settings import Settings

# 同一型号只加载一次；批量转写时省去每个文件反复加载模型的等待
_MODELS: dict[str, object] = {}


# ffmpeg 垫片目录：imageio_ffmpeg 的二进制文件名不规则（如 ffmpeg-win-x86_64-v7.1.exe），
# subprocess 按 "ffmpeg" 找不到它——复制改名一份放这里（一次性 88MB），PATH 指向此目录
_FFMPEG_SHIM_DIR = Path.home() / ".lyrics_transcriber" / "bin"


def _ensure_ffmpeg() -> None:
    """whisper 靠 PATH 里的 ffmpeg 读音频；缺它时借 imageio_ffmpeg 的静态版。

    imageio_ffmpeg 是纯 pip 包（自带 ffmpeg），装增强包时顺带
    ``pip install imageio-ffmpeg`` 即可，免去用户单独安装系统 ffmpeg。
    它自带的二进制文件名不规则，subprocess 按 "ffmpeg" 调不到——复制成
    标准名放进垫片目录，PATH 前置该目录。
    """
    if shutil.which("ffmpeg"):
        return
    try:
        import imageio_ffmpeg

        source = Path(imageio_ffmpeg.get_ffmpeg_exe())
    except Exception:
        return  # 两边都没有时，让 whisper 在读音频处抛出带指引的 TranscribeError

    shim = _FFMPEG_SHIM_DIR / ("ffmpeg.exe" if os.name == "nt" else "ffmpeg")
    try:
        if not shim.exists():
            _FFMPEG_SHIM_DIR.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, shim)
    except OSError:
        return
    os.environ["PATH"] = str(_FFMPEG_SHIM_DIR) + os.pathsep + os.environ.get("PATH", "")


def transcribe_local(path, settings: Settings) -> Transcription:
    """用本机 whisper 模型转写；任何环境问题都转成带指引的 TranscribeError。"""
    try:
        import whisper_timestamped
    except Exception as exc:
        raise TranscribeError(
            "本机转写引擎未安装：请先在命令行执行 "
            "pip install whisper-timestamped imageio-ffmpeg"
            "（详见 README 的「AI 增强包」一节）"
        ) from exc

    name = str(getattr(settings, "local_model", "") or "base")
    _ensure_ffmpeg()
    try:
        model = _MODELS.get(name)
        if model is None:
            model = whisper_timestamped.load_model(name)
            _MODELS[name] = model
        result = whisper_timestamped.transcribe(model, str(Path(path)))
    except Exception as exc:  # ffmpeg 缺失、音频解码失败、内存不足等
        raise TranscribeError(f"本机转写失败: {exc}") from exc

    words = []
    for segment in result.get("segments", []):
        segment_words = [
            word for word in segment.get("words", [])
            if str(word.get("word") or word.get("text") or "").strip()
        ]
        for index, word in enumerate(segment_words):
            entry = dict(word)
            if index == 0:
                entry["sentence_start"] = True  # 拼行时优先按分段边界断行
            words.append(entry)
    return build_lines(words=words, text=result.get("text") or "")

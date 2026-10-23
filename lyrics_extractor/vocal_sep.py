"""人声分离（Demucs，可选增强依赖）：把人声从伴奏里剥出来再交给转写引擎。

依赖 ``demucs`` 与 ``torch``（pip 安装，体积约 1GB 级），未安装时自动跳过——
人声分离只是提升转写准确率的锦上添花，绝不该挡住主流程。分离结果写成单声道
FLAC（比原曲小得多，方便上传），放在临时目录里，用完由调用方整体删掉。
"""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path

# 分离模型：htdemucs 是 Demucs v4 默认模型，人声质量与速度的平衡点
DEMUCS_MODEL = "htdemucs"

_MISSING = False  # 已确认没装依赖，不再每个文件都白试一遍导入


def is_available() -> bool:
    """检测 Demucs 是否可导入；结果缓存，避免批量处理时反复白试。"""
    global _MISSING
    if _MISSING:
        return False
    try:
        import demucs.api  # noqa: F401
    except Exception:
        _MISSING = True
        return False
    return True


def separate_vocals(path, log=None) -> tuple[Path, Path] | None:
    """分离人声，返回 (人声 FLAC, 临时目录)；没装依赖或分离失败时返回 None。

    CPU 分离一首歌通常要几分钟，属于「用户明确勾选增强」才值得花的开销。
    """
    log = log or (lambda msg: None)
    if not is_available():
        log("未安装 Demucs（人声分离增强），直接转写原音频")
        return None

    from demucs.api import Separator

    tmp_dir = Path(tempfile.mkdtemp(prefix="lyrics_vocals_"))
    done = False
    try:
        separator = Separator(model=DEMUCS_MODEL, device="cpu", progress=False)
        _origin, separated = separator.separate_audio_file(str(path))
        vocals = separated.get("vocals")
        if vocals is None:
            log("Demucs 没有产出人声轨，改用原音频转写")
            return None
        out = tmp_dir / (Path(path).stem + "-vocals.flac")
        _write_mono_flac(vocals, separator.samplerate, out)
        done = True
        return out, tmp_dir
    except Exception as exc:
        log(f"人声分离失败（{exc}），改用原音频转写")
        return None
    finally:
        if not done:
            shutil.rmtree(tmp_dir, ignore_errors=True)


def _write_mono_flac(tensor, samplerate: int, out: Path) -> None:
    """把 Demucs 输出的 (声道, 采样) 张量混成单声道写成 FLAC。"""
    import soundfile as sf

    mono = tensor
    try:
        import torch

        if isinstance(tensor, torch.Tensor):
            mono = tensor.detach().cpu().numpy()
    except ImportError:
        pass  # demucs 正常安装必然带 torch，这里只是防御性兜底
    if mono.ndim == 2:
        mono = mono.mean(axis=0)
    sf.write(str(out), mono, samplerate, format="FLAC")

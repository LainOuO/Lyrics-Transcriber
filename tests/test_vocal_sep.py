"""人声分离封装：非 WAV 预转、HF 离线开关、失败清理与产出。"""

import os
import shutil
import sys
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from lyrics_transcriber import vocal_sep


class FakeSeparator:
    calls: list = []
    samplerate = 44100

    def __init__(self, model=None, device=None, progress=False):
        self.model = model

    def separate_audio_file(self, path):
        FakeSeparator.calls.append(path)
        return None, {"vocals": np.zeros((2, 100), dtype="float32")}


@pytest.fixture
def fake_demucs(monkeypatch):
    """注入假 demucs.api（真实依赖 1GB 级，单测不碰）。"""
    api = SimpleNamespace(Separator=FakeSeparator)
    monkeypatch.setitem(sys.modules, "demucs", SimpleNamespace(api=api))
    monkeypatch.setitem(sys.modules, "demucs.api", api)
    monkeypatch.setattr(vocal_sep, "_MISSING", False)
    monkeypatch.setitem(
        sys.modules, "torch", SimpleNamespace(Tensor=type("Tensor", (), {}))
    )  # _write_mono_flac 只用 isinstance，假 Tensor 恒 False 即可
    FakeSeparator.calls = []
    return FakeSeparator


@pytest.fixture
def fake_soundfile(monkeypatch):
    """替身 soundfile：read 返回 4 帧立体声，write 记录目标路径。"""
    written = []
    fake = SimpleNamespace(
        read=lambda path, always_2d=True: (
            np.zeros((4, 2), dtype="float32"), 44100),
        write=lambda path, data, sr, format=None: written.append(str(path)),
    )
    monkeypatch.setitem(sys.modules, "soundfile", fake)
    return written


def test_non_wav_input_converted_to_wav_first(
        fake_demucs, fake_soundfile, monkeypatch, tmp_path):
    """FLAC 等 non-WAV 先用 soundfile 转 WAV 再喂 demucs（绕开 ffmpeg 依赖）。"""
    monkeypatch.delenv("HF_HUB_OFFLINE", raising=False)
    src = tmp_path / "歌.flac"
    src.write_bytes(b"fake-flac")
    result = vocal_sep.separate_vocals(src)
    assert result is not None
    vocals_flac, tmp_dir = result
    # 分离器收到的是临时目录里的 WAV
    assert len(fake_demucs.calls) == 1
    assert Path(fake_demucs.calls[0]).suffix == ".wav"
    assert Path(fake_demucs.calls[0]).parent == tmp_dir
    # 产出：人声 FLAC 与转换用 WAV 各写了一次
    assert sorted(p.endswith("-vocals.flac") for p in fake_soundfile) == [False, True]
    assert str(vocals_flac).endswith("-vocals.flac")
    # huggingface 被墙的 5 分钟超时惩罚靠这个开关跳过
    assert os.environ.get("HF_HUB_OFFLINE") == "1"
    shutil.rmtree(tmp_dir, ignore_errors=True)


def test_wav_input_passes_through(fake_demucs, fake_soundfile, tmp_path):
    src = tmp_path / "直传.wav"
    src.write_bytes(b"RIFF")
    result = vocal_sep.separate_vocals(src)
    assert result is not None
    assert fake_demucs.calls == [str(src)]  # WAV 不做转换，原路径直传
    shutil.rmtree(result[1], ignore_errors=True)


def test_failure_returns_none_and_cleans_up(
        fake_demucs, fake_soundfile, monkeypatch, tmp_path):
    monkeypatch.setattr(
        vocal_sep.tempfile, "mkdtemp",
        lambda prefix=None: str(tmp_path / "vocals_tmp"),
    )

    def boom(self, path):
        raise RuntimeError("模型炸了")

    monkeypatch.setattr(FakeSeparator, "separate_audio_file", boom)
    src = tmp_path / "s.wav"
    src.write_bytes(b"RIFF")
    assert vocal_sep.separate_vocals(src) is None
    assert not (tmp_path / "vocals_tmp").exists()  # 临时目录已清


def test_missing_vocals_stem_returns_none(fake_demucs, fake_soundfile, monkeypatch, tmp_path):
    monkeypatch.setattr(
        FakeSeparator, "separate_audio_file",
        lambda self, path: (None, {"drums": np.zeros((2, 8))}),
    )
    src = tmp_path / "s.wav"
    src.write_bytes(b"RIFF")
    assert vocal_sep.separate_vocals(src) is None


def test_unavailable_returns_none_without_import(monkeypatch, tmp_path):
    monkeypatch.setattr(vocal_sep, "_MISSING", True)
    src = tmp_path / "s.wav"
    src.write_bytes(b"RIFF")
    assert vocal_sep.separate_vocals(src) is None

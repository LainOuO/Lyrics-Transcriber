"""本地 Whisper 引擎：词字段两种形状、ffmpeg 自动借用、缺依赖指引。"""

import os
import sys
from pathlib import Path

import pytest

from lyrics_transcriber import whisper_local
from lyrics_transcriber.whisper_local import TranscribeError, transcribe_local

FAKE_RESULT = {
    "text": "第一句词 第二句词",
    "segments": [
        {"text": "第一句词", "start": 1.0, "end": 2.0, "words": [
            # 真实形状（1.15.9）：字段叫 text，word 恒为 None
            {"word": None, "text": "第一", "start": 1.0, "end": 1.4},
            {"word": None, "text": "句词", "start": 1.4, "end": 2.0},
        ]},
        {"text": "第二句词", "start": 5.0, "end": 6.0, "words": [
            # 文档形状：字段叫 word
            {"word": "第二", "text": None, "start": 5.0, "end": 5.4},
            {"word": "句词", "text": None, "start": 5.4, "end": 6.0},
        ]},
    ],
}


class _Settings:
    """只带 local_model 字段的极简设置对象（引擎也只读这一个字段）。"""

    def __init__(self, local_model):
        self.local_model = local_model


@pytest.fixture
def fake_whisper(monkeypatch):
    """注入假 whisper_timestamped：记录加载的型号，返回固定结构的结果。"""
    loaded = []

    class FakeWhisper:
        @staticmethod
        def load_model(name, device=None):
            loaded.append(name)
            return object()

        @staticmethod
        def transcribe(model, path, language=None):
            return FAKE_RESULT

    monkeypatch.setitem(sys.modules, "whisper_timestamped", FakeWhisper)
    monkeypatch.setattr(whisper_local, "_MODELS", {})
    monkeypatch.setattr(whisper_local, "_ensure_ffmpeg", lambda: None)
    return loaded


def test_words_from_both_field_shapes(fake_whisper):
    """1.15.9 的 text 字段与文档的 word 字段都要能取到词，拼出同步行。"""
    result = transcribe_local("song.mp3", _Settings("base"))
    assert result.lines == [(1000, "第一句词"), (5000, "第二句词")]
    assert fake_whisper == ["base"]


def test_default_model_is_base(fake_whisper):
    """实测 small 在音乐上又慢又有幻觉（实测 2026-10），默认型号定为 base。"""
    transcribe_local("song.mp3", _Settings(""))
    assert fake_whisper == ["base"]


def test_missing_engine_gives_install_guidance(monkeypatch):
    monkeypatch.setitem(sys.modules, "whisper_timestamped", None)
    with pytest.raises(TranscribeError) as excinfo:
        transcribe_local("song.mp3", _Settings("base"))
    assert "pip install" in str(excinfo.value)


def test_ensure_ffmpeg_borrows_imageio(monkeypatch):
    """PATH 里没有 ffmpeg 时，自动把 imageio_ffmpeg 自带的静态版加进 PATH。"""
    monkeypatch.setattr(whisper_local.shutil, "which", lambda name: None)

    fake_exe = Path(r"C:\fake\tools\ffmpeg.exe")

    class FakeImageio:
        @staticmethod
        def get_ffmpeg_exe():
            return str(fake_exe)

    monkeypatch.setitem(sys.modules, "imageio_ffmpeg", FakeImageio)
    monkeypatch.setenv("PATH", r"C:\Windows\System32")
    whisper_local._ensure_ffmpeg()
    assert os.environ["PATH"].startswith(str(fake_exe.parent))


def test_ensure_ffmpeg_noop_when_present(monkeypatch):
    monkeypatch.setattr(whisper_local.shutil, "which", lambda name: "found")
    monkeypatch.setenv("PATH", r"C:\Windows\System32")
    whisper_local._ensure_ffmpeg()
    assert os.environ["PATH"] == r"C:\Windows\System32"

"""AI 听歌转写：拼行逻辑、云端客户端（请求阶梯 / 重试 / 错误归类）与引擎调度。"""

import pytest
import requests

from lyrics_extractor import ai_transcribe
from lyrics_extractor.ai_transcribe import (
    TranscribeError,
    build_lines,
    transcribe_cloud,
    transcriptions_url,
)
from lyrics_extractor.settings import (
    ENGINE_LOCAL,
    PROVIDER_DASHSCOPE,
    PROVIDER_OPENAI,
    Settings,
)


class FakeResp:
    def __init__(self, status_code=200, payload=None):
        self.status_code = status_code
        self._payload = payload

    def json(self):
        if isinstance(self._payload, Exception):
            raise self._payload
        return self._payload


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    """重试退避不该让测试真的等。"""
    slept = []
    monkeypatch.setattr(ai_transcribe.time, "sleep", slept.append)
    return slept


@pytest.fixture
def audio(tmp_path):
    """一个小的假音频文件（客户端要 stat 大小并打开上传）。"""
    path = tmp_path / "song.mp3"
    path.write_bytes(b"\xff\xfb" + b"\x00" * 100)
    return path


@pytest.fixture
def post_api(monkeypatch):
    """按顺序消费预设响应，并记录每次请求的参数。"""
    calls = []
    queue = []

    def _post(url, data=None, files=None, headers=None, timeout=None):
        calls.append({"url": url, "data": dict(data or {}), "headers": headers})
        assert queue, "未预期的请求"
        return queue.pop(0)

    monkeypatch.setattr(ai_transcribe.requests, "post", _post)
    return calls, queue


# ----------------------------------------------------------------------
# 拼行逻辑：词级 / 分段 / 纯文本


def word(text, start, end=None):
    return {"word": text, "start": start, "end": end if end is not None else start + 0.3}


def test_words_split_on_sentence_punctuation():
    result = build_lines(words=[
        word("刮", 1.0), word("风", 1.2), word("这", 1.4), word("天", 1.6),
        word("我", 3.0), word("试", 3.2), word("过", 3.4), word("。", 3.6),
    ])
    # 句号后断行；孤立句号并到行尾而不是另起一行
    assert result.lines == [(1000, "刮风这天"), (3000, "我试过。")]


def test_words_split_on_phrase_gap():
    result = build_lines(words=[
        word("第一", 1.0, 1.4), word("句", 1.5, 1.8),
        word("第二", 5.0, 5.4), word("句", 5.5, 5.8),  # 中间停了 3 秒多
    ])
    assert result.lines == [(1000, "第一句"), (5000, "第二句")]


def test_words_split_on_line_weight():
    # 16 个汉字（weight 32 > 上限 30）应断成两行：前 15 个一行，第 16 个另一行
    chars = [word("字", 1.0 + i * 0.5) for i in range(16)]
    result = build_lines(words=chars)
    assert len(result.lines) == 2
    assert result.lines[0][1] == "字" * 15
    assert result.lines[1][1] == "字"


def test_words_drop_consecutive_duplicates():
    # Whisper 在音乐上的「复读机」幻觉：同一句连转多遍，只留一遍
    result = build_lines(words=[
        word("啊", 1.0), word("啊", 2.5),  # 间隔 1.2s，各自成行
        word("正", 5.0), word("文", 5.2),  # 正文另成一行
    ])
    assert [body for _, body in result.lines] == ["啊", "正文"]


def test_segments_each_become_one_line():
    result = build_lines(
        segments=[
            {"text": " 前奏之后的第一句 ", "start": 12.3, "end": 15.0},
            {"text": "", "start": 20.0, "end": 21.0},  # 空段跳过
            {"text": "第二句", "start": 21.5, "end": 24.0},
        ],
        text="前奏之后的第一句 第二句",
    )
    assert result.lines == [(12300, "前奏之后的第一句"), (21500, "第二句")]


def test_text_with_newlines_kept_as_plain():
    result = build_lines(text="第一行\n\n第二行\n")
    assert result.lines is None
    assert result.plain_lines == ["第一行", "第二行"]


def test_single_blob_text_split_by_sentence_punctuation():
    result = build_lines(text="一句话。两句话！三句话？")
    assert result.plain_lines == ["一句话。", "两句话！", "三句话？"]


def test_words_take_priority_over_segments_and_text():
    result = build_lines(
        words=[word("词", 1.0)],
        segments=[{"text": "分段", "start": 50.0}],
        text="文本",
    )
    assert result.lines == [(1000, "词")]


def test_empty_everything_marks_empty():
    assert build_lines().empty is True
    assert build_lines(text="   \n  ").empty is True


def test_word_timestamps_survive_roundtrip_through_lrc_format():
    result = build_lines(words=[word("你", 6.5, 6.9), word("好", 7.0, 7.4)])
    assert result.lines == [(6500, "你好")]


# ----------------------------------------------------------------------
# 接口地址拼接


def test_url_adds_v1_when_missing():
    assert transcriptions_url("https://api.example.com") == \
        "https://api.example.com/v1/audio/transcriptions"


def test_url_accepts_base_already_ending_with_v1():
    assert transcriptions_url("https://api.groq.com/openai/v1") == \
        "https://api.groq.com/openai/v1/audio/transcriptions"


def test_url_falls_back_to_default_when_blank():
    assert transcriptions_url("") == transcriptions_url(None)


# ----------------------------------------------------------------------
# 云端客户端：请求阶梯


def KEY():
    return Settings(api_key="sk-test")


def test_cloud_requires_api_key(audio):
    with pytest.raises(TranscribeError) as excinfo:
        transcribe_cloud(audio, Settings(api_key="  "))
    assert "未配置" in str(excinfo.value)


def test_cloud_rejects_oversize_file(audio, monkeypatch):
    monkeypatch.setattr(ai_transcribe, "MAX_UPLOAD_BYTES", 8)
    with pytest.raises(TranscribeError) as excinfo:
        transcribe_cloud(audio, KEY())
    assert "超过云端接口单文件上限" in str(excinfo.value)


def test_cloud_happy_path_with_word_timestamps(audio, post_api):
    calls, queue = post_api
    queue.append(FakeResp(200, {
        "text": " 你好 世界 ",
        "words": [word("你好", 1.0), word("世界", 5.0)],  # 停顿大，断成两行
        "segments": [],
    }))
    result = transcribe_cloud(audio, KEY())
    assert result.lines == [(1000, "你好"), (5000, "世界")]
    assert calls[0]["url"] == "https://api.groq.com/openai/v1/audio/transcriptions"
    assert calls[0]["data"]["model"] == "whisper-large-v3-turbo"
    assert calls[0]["data"]["response_format"] == "verbose_json"
    assert calls[0]["data"]["timestamp_granularities[]"] == "word"
    assert calls[0]["headers"]["Authorization"] == "Bearer sk-test"


def test_cloud_ladder_falls_back_when_verbose_json_rejected(audio, post_api):
    """不认 response_format 的服务商返回 400：逐级降级到普通请求。"""
    calls, queue = post_api
    queue.append(FakeResp(400, {"message": "response_format not supported"}))
    queue.append(FakeResp(400, {"message": "response_format not supported"}))
    queue.append(FakeResp(200, {"text": "第一行\n第二行"}))
    result = transcribe_cloud(audio, KEY())
    assert result.plain_lines == ["第一行", "第二行"]
    assert result.lines is None
    formats = [c["data"].get("response_format") for c in calls]
    gran = [c["data"].get("timestamp_granularities[]") for c in calls]
    assert formats == ["verbose_json", "verbose_json", None]
    assert gran == ["word", None, None]


def test_cloud_ladder_exhausted_reports_server_hint(audio, post_api):
    calls, queue = post_api
    queue.append(FakeResp(400, {"code": 20012, "message": "Model does not exist."}))
    queue.append(FakeResp(400, {"code": 20012, "message": "Model does not exist."}))
    queue.append(FakeResp(400, {"code": 20012, "message": "Model does not exist."}))
    with pytest.raises(TranscribeError) as excinfo:
        transcribe_cloud(audio, KEY())
    assert "400" in str(excinfo.value)
    assert "Model does not exist" in str(excinfo.value)
    assert len(calls) == 3  # 400 不重试，只降级


# ----------------------------------------------------------------------
# 云端客户端：错误归类与重试


def test_cloud_401_means_bad_key(audio, post_api):
    _, queue = post_api
    queue.append(FakeResp(401, {"message": "invalid api key"}))
    with pytest.raises(TranscribeError) as excinfo:
        transcribe_cloud(audio, KEY())
    assert "密钥无效" in str(excinfo.value)


def test_cloud_403_reports_model_disabled_hint(audio, post_api):
    """硅基流动下架 SenseVoiceSmall 时就返回 403 Model disabled（spike 实测）。"""
    _, queue = post_api
    queue.append(FakeResp(403, {"code": 30003, "message": "Model disabled."}))
    with pytest.raises(TranscribeError) as excinfo:
        transcribe_cloud(audio, KEY())
    assert "模型不可用" in str(excinfo.value)
    assert "Model disabled" in str(excinfo.value)


def test_cloud_404_means_wrong_base_url(audio, post_api):
    _, queue = post_api
    queue.append(FakeResp(404))
    with pytest.raises(TranscribeError) as excinfo:
        transcribe_cloud(audio, KEY())
    assert "接口地址" in str(excinfo.value)


def test_cloud_413_means_file_too_large(audio, post_api):
    _, queue = post_api
    queue.append(FakeResp(413))
    with pytest.raises(TranscribeError) as excinfo:
        transcribe_cloud(audio, KEY())
    assert "大小上限" in str(excinfo.value)


def test_cloud_transient_503_retried_then_succeeds(audio, post_api, no_sleep):
    _, queue = post_api
    queue.append(FakeResp(503, {"message": "busy"}))
    queue.append(FakeResp(200, {"text": "ok"}))
    result = transcribe_cloud(audio, KEY())
    assert result.plain_lines == ["ok"]
    assert no_sleep == [ai_transcribe.RETRY_BACKOFF]


def test_cloud_persistent_503_reports_retries(audio, post_api):
    _, queue = post_api
    queue.extend([FakeResp(503)] * ai_transcribe.RETRY_ATTEMPTS)
    with pytest.raises(TranscribeError) as excinfo:
        transcribe_cloud(audio, KEY())
    assert f"已重试 {ai_transcribe.RETRY_ATTEMPTS} 次" in str(excinfo.value)


def test_cloud_network_error_retried(audio, post_api, monkeypatch):
    _, queue = post_api

    def flaky(*args, **kwargs):
        if not queue:
            queue.append(FakeResp(200, {"text": "网络恢复后转出的文本"}))
            raise requests.ConnectionError("断网了")
        return queue.pop(0)

    monkeypatch.setattr(ai_transcribe.requests, "post", flaky)
    result = transcribe_cloud(audio, KEY())
    assert result.plain_lines == ["网络恢复后转出的文本"]


def test_cloud_bad_json_raises(audio, post_api):
    _, queue = post_api
    queue.append(FakeResp(200, ValueError("not json")))
    with pytest.raises(TranscribeError) as excinfo:
        transcribe_cloud(audio, KEY())
    assert "无法解析" in str(excinfo.value)


def test_cloud_non_dict_payload_raises(audio, post_api):
    _, queue = post_api
    queue.append(FakeResp(200, ["不是", "字典"]))
    with pytest.raises(TranscribeError):
        transcribe_cloud(audio, KEY())


def test_cloud_empty_text_is_empty_transcription(audio, post_api):
    """纯音乐/无人声：200 但啥也没转出来，属于「未识别」而不是错误。"""
    _, queue = post_api
    queue.append(FakeResp(200, {"text": "   "}))
    result = transcribe_cloud(audio, KEY())
    assert result.empty is True


# ----------------------------------------------------------------------
# 统一入口：人声分离 + 引擎调度


@pytest.fixture
def engines(monkeypatch, tmp_path):
    """替换人声分离与两个引擎，记录调用；默认人声分离返回 None（跳过）。"""
    from lyrics_extractor import vocal_sep, whisper_local

    seen = {}

    def fake_separate(path, log=None):
        seen["separate"] = path
        return None

    def fake_cloud(path, settings, timeout=None):
        seen["cloud"] = path
        return ai_transcribe.Transcription(text="云", plain_lines=["云"])

    def fake_local(path, settings):
        seen["local"] = path
        return ai_transcribe.Transcription(text="本", plain_lines=["本"])

    monkeypatch.setattr(vocal_sep, "is_available", lambda: True)
    monkeypatch.setattr(vocal_sep, "separate_vocals", fake_separate)
    monkeypatch.setattr(ai_transcribe, "transcribe_cloud", fake_cloud)
    monkeypatch.setattr(whisper_local, "transcribe_local", fake_local)
    return seen


def test_dispatch_openai_provider(audio, engines):
    seen = engines
    result = ai_transcribe.transcribe_audio(
        audio, Settings(separate_vocals=False, provider=PROVIDER_OPENAI))
    assert str(seen["cloud"]) == str(audio)
    assert "separate" not in seen
    assert result.plain_lines == ["云"]


def test_dispatch_dashscope_provider(audio, engines, monkeypatch):
    from lyrics_extractor import dashscope_asr

    seen = engines

    def fake_dashscope(path, settings):
        seen["dashscope"] = path
        return ai_transcribe.Transcription(text="百", plain_lines=["百"])

    monkeypatch.setattr(dashscope_asr, "transcribe", fake_dashscope)
    result = ai_transcribe.transcribe_audio(audio, Settings(separate_vocals=False))
    assert str(seen["dashscope"]) == str(audio)
    assert "cloud" not in seen  # 百炼不该再走 OpenAI 兼容客户端
    assert result.plain_lines == ["百"]


def test_dispatch_local_engine(audio, engines):
    seen = engines
    ai_transcribe.transcribe_audio(
        audio, Settings(engine=ENGINE_LOCAL, separate_vocals=False))
    assert str(seen["local"]) == str(audio)
    assert "cloud" not in seen


def test_vocal_separation_feeds_engine_and_cleans_up(audio, engines, monkeypatch, tmp_path):
    import shutil as _shutil

    from lyrics_extractor import vocal_sep

    tmp_dir = tmp_path / "vocals_tmp"
    tmp_dir.mkdir()
    vocals = tmp_dir / "vocals.flac"
    vocals.write_bytes(b"flac")
    monkeypatch.setattr(
        vocal_sep, "separate_vocals", lambda path, log=None: (vocals, tmp_dir)
    )

    class RaisingCloud:
        def __call__(self, path, settings, timeout=None):
            engines["cloud"] = path
            raise TranscribeError("转写服务炸了")

    monkeypatch.setattr(ai_transcribe, "transcribe_cloud", RaisingCloud())
    with pytest.raises(TranscribeError):
        ai_transcribe.transcribe_audio(
            audio, Settings(provider=PROVIDER_OPENAI))
    # 引擎吃到的是分离出的人声轨
    assert str(engines["cloud"]) == str(vocals)
    # 转写失败也要把临时目录清掉
    assert not tmp_dir.exists()


def test_separation_skipped_when_unavailable(audio, engines, monkeypatch):
    from lyrics_extractor import vocal_sep

    monkeypatch.setattr(vocal_sep, "is_available", lambda: False)
    ai_transcribe.transcribe_audio(
        audio, Settings(separate_vocals=True, provider=PROVIDER_OPENAI))
    assert "cloud" in engines

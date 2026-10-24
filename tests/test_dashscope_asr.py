"""百炼（DashScope）适配器：上传中转、异步任务、Qwen 聊天协议与地址防护。"""

import socket

import pytest

import lyrics_extractor.dashscope_asr as ds
from lyrics_extractor.dashscope_asr import (
    TranscribeError,
    _guard_aliyun_url,
    _guard_fixed_url,
    transcribe,
    transcribe_paraformer,
)
from lyrics_extractor.settings import Settings

UPLOAD_HOST = "https://dashscope-file-test.oss-cn-beijing.aliyuncs.com"
RESULT_URL = "https://dashscope-result-test.oss-cn-beijing.aliyuncs.com/r.json"

# 在 autouse 桩生效前抓一份真实实现，供 DNS 边界测试直接调用
_REAL_ASSERT_PUBLIC_HOST = ds._assert_public_host


@pytest.fixture
def audio(tmp_path):
    """一个小的假音频文件（上传与凭证流程要打开它）。"""
    path = tmp_path / "song.mp3"
    path.write_bytes(b"\xff\xfb" + b"\x00" * 100)
    return path


class FakeResp:
    def __init__(self, status_code=200, payload=None):
        self.status_code = status_code
        self._payload = payload

    def json(self):
        if isinstance(self._payload, Exception):
            raise self._payload
        return self._payload


@pytest.fixture(autouse=True)
def no_sleep_no_dns(monkeypatch):
    """重试退避与轮询间隔不真等；地址防护的 DNS 解析也桩掉，保持单测离线。"""
    slept = []
    monkeypatch.setattr(ds.time, "sleep", slept.append)
    resolved = []
    monkeypatch.setattr(
        ds, "_assert_public_host", lambda host: resolved.append(host)
    )
    return {"slept": slept, "resolved": resolved}


@pytest.fixture
def fake_net(monkeypatch):
    """按「方法 + URL 片段」路由预设响应，并记录所有出站请求。"""
    calls = []
    routes = []

    def when(method, substr, *responses):
        routes.append((method, substr, list(responses)))

    def _respond(method, url):
        for m, substr, responses in routes:
            if m == method and substr in url:
                return responses.pop(0) if len(responses) > 1 else responses[0]
        raise AssertionError(f"未预期的请求: {method} {url}")

    def fake_request(method, url, **kwargs):
        calls.append({"method": method, "url": url, "kwargs": kwargs})
        return _respond(method, url)

    monkeypatch.setattr(ds.requests, "request", fake_request)
    monkeypatch.setattr(
        ds.requests, "post", lambda url, **kw: fake_request("POST", url, **kw)
    )
    monkeypatch.setattr(
        ds.requests, "get", lambda url, **kw: fake_request("GET", url, **kw)
    )
    return calls, when


def policy_response():
    return FakeResp(200, {"data": {
        "upload_host": UPLOAD_HOST,
        "upload_dir": "dashscope-instant/t/2026",
        "oss_access_key_id": "id",
        "signature": "sig",
        "policy": "p",
        "x_oss_object_acl": "private",
        "x_oss_forbid_overwrite": "true",
    }})


def result_json(with_words=True):
    sentence = {"text": "第一句", "begin_time": 1000, "end_time": 2000}
    if with_words:
        sentence["words"] = [
            {"text": "第一", "punctuation": "，", "begin_time": 1000, "end_time": 1500},
            {"text": "句", "begin_time": 1500, "end_time": 2000},
        ]
    sentence2 = {"text": "第二句", "begin_time": 5000, "end_time": 6000}
    return {"transcripts": [{"sentences": [sentence, sentence2]}]}


def paraformer_flow(fake_net, *, poll_responses=None, result=None):
    """预置 Paraformer 全链路响应。"""
    calls, when = fake_net
    when("GET", "/api/v1/uploads", policy_response())
    when("POST", "dashscope-file-test", FakeResp(200))
    when("POST", "/services/audio/asr/transcription",
         FakeResp(200, {"output": {"task_id": "task-1"}}))
    when("POST", "/api/v1/tasks/",
         *(poll_responses or [FakeResp(200, {"output": {"task_status": "SUCCEEDED",
                                                        "results": [{"transcription_url": RESULT_URL}]}})]))
    when("GET", "dashscope-result-test",
         FakeResp(200, result if result is not None else result_json()))
    return calls


def test_paraformer_words_become_synced_lines(audio, fake_net, no_sleep_no_dns):
    calls = paraformer_flow(fake_net)
    result = transcribe_paraformer(audio, Settings(api_key="sk-dash"))
    # 词带随附标点：句子内不断行，两句各自成行（句边界标记生效）
    assert result.lines == [(1000, "第一，句"), (5000, "第二句")]
    # 关键请求头与模型都要对：异步任务 + oss:// 地址解析
    create = next(c for c in calls if "/services/audio/asr/transcription" in c["url"])
    assert create["kwargs"]["headers"]["X-DashScope-Async"] == "enable"
    assert create["kwargs"]["headers"]["X-DashScope-OssResourceResolve"] == "enable"
    assert create["kwargs"]["json"]["model"] == "paraformer-v2"
    assert create["kwargs"]["json"]["input"]["file_urls"][0].startswith("oss://")
    # 上传地址与结果下载地址都要过域名防护
    resolved = {host for host in no_sleep_no_dns["resolved"]}
    assert "dashscope-file-test.oss-cn-beijing.aliyuncs.com" in resolved
    assert "dashscope-result-test.oss-cn-beijing.aliyuncs.com" in resolved


def test_to_words_marks_sentence_boundaries():
    """每句第一个词要带 sentence_start 标记（拼行优先按句子边界断行）。"""
    from lyrics_extractor.dashscope_asr import _to_words

    words = _to_words([
        {"text": "一句", "begin_time": 1000, "end_time": 1500,
         "words": [{"text": "一", "begin_time": 1000, "end_time": 1200},
                   {"text": "句", "punctuation": "，", "begin_time": 1200,
                    "end_time": 1500}]},
        {"text": "二句", "begin_time": 3000, "end_time": 3500,
         "words": [{"text": "", "begin_time": 3000, "end_time": 3100},  # 空词跳过
                   {"text": "二", "begin_time": 3100, "end_time": 3300},
                   {"text": "句", "begin_time": 3300, "end_time": 3500}]},
    ])
    # 句首标记落在每句首个实词上（空词被跳过也不影响）
    assert words[0].get("sentence_start") is True
    assert words[1].get("sentence_start") is None
    assert words[2].get("sentence_start") is True  # 空词之后的"二"扛标记
    assert words[3].get("sentence_start") is None


def test_paraformer_sentence_fallback_without_words(audio, fake_net):
    paraformer_flow(fake_net, result=result_json(with_words=False))
    result = transcribe_paraformer(audio, Settings(api_key="sk-dash"))
    # 没有词级时间戳时一句一行，行时间 = 句子开始
    assert result.lines == [(1000, "第一句"), (5000, "第二句")]


def test_paraformer_empty_result_is_empty(audio, fake_net):
    paraformer_flow(fake_net, result={"transcripts": [{"sentences": []}]})
    result = transcribe_paraformer(audio, Settings(api_key="sk-dash"))
    assert result.empty is True


def test_paraformer_requires_key(audio):
    with pytest.raises(TranscribeError) as excinfo:
        transcribe_paraformer(audio, Settings(api_key=" "))
    assert "未配置百炼密钥" in str(excinfo.value)


def test_paraformer_task_failed_reports_reason(audio, fake_net):
    paraformer_flow(fake_net, poll_responses=[
        FakeResp(200, {"output": {"task_status": "RUNNING"}}),
        FakeResp(200, {"output": {"task_status": "FAILED"},
                       "code": "Arrearage", "message": "账户欠费"}),
    ])
    with pytest.raises(TranscribeError) as excinfo:
        transcribe_paraformer(audio, Settings(api_key="sk-dash"))
    assert "任务失败" in str(excinfo.value)
    assert "账户欠费" in str(excinfo.value)


def test_dispatch_uses_paraformer(audio, fake_net):
    paraformer_flow(fake_net)
    assert transcribe(audio, Settings(api_key="sk-dash")).lines is not None


def test_nested_openai_style_error_message_surfaces(audio, fake_net):
    """兼容模式接口的错误体是 error.message 嵌套结构，原因要透传给用户。"""
    _, when = fake_net
    when("GET", "/api/v1/uploads", FakeResp(400, {
        "error": {"message": "The dedicated task asr does not support this input.",
                  "code": "invalid_parameter_error"},
    }))
    with pytest.raises(TranscribeError) as excinfo:
        transcribe_paraformer(audio, Settings(api_key="sk-dash"))
    assert "does not support this input" in str(excinfo.value)


def test_401_means_bad_key(audio, fake_net):
    _, when = fake_net
    when("GET", "/api/v1/uploads", FakeResp(401))
    with pytest.raises(TranscribeError) as excinfo:
        transcribe_paraformer(audio, Settings(api_key="sk-bad"))
    assert "密钥无效" in str(excinfo.value)


def test_transient_503_retried_then_succeeds(audio, fake_net, no_sleep_no_dns):
    _, when = fake_net
    when("GET", "/api/v1/uploads",
         FakeResp(503, {"message": "busy"}), policy_response())
    when("POST", "dashscope-file-test", FakeResp(200))
    when("POST", "/services/audio/asr/transcription",
         FakeResp(200, {"output": {"task_id": "task-1"}}))
    when("POST", "/api/v1/tasks/",
         FakeResp(200, {"output": {"task_status": "SUCCEEDED",
                                   "results": [{"transcription_url": RESULT_URL}]}}))
    when("GET", "dashscope-result-test", FakeResp(200, result_json()))
    result = transcribe_paraformer(audio, Settings(api_key="sk-dash"))
    assert result.lines is not None
    assert no_sleep_no_dns["slept"] == [ds.RETRY_BACKOFF]


def test_server_error_message_surfaces(audio, fake_net):
    _, when = fake_net
    when("GET", "/api/v1/uploads",
         FakeResp(400, {"code": "InvalidParameter", "message": "model not found"}))
    with pytest.raises(TranscribeError) as excinfo:
        transcribe_paraformer(audio, Settings(api_key="sk-dash"))
    assert "400" in str(excinfo.value)
    assert "model not found" in str(excinfo.value)


# ----------------------------------------------------------------------
# 地址防护（SSRF / DNS rebinding）


def test_guard_aliyun_url_rejects_http_and_foreign_hosts():
    with pytest.raises(TranscribeError):
        _guard_aliyun_url("http://evil.aliyuncs.com/f", "上传")
    with pytest.raises(TranscribeError):
        _guard_aliyun_url("https://evil.com/f", "结果下载")
    assert _guard_aliyun_url(UPLOAD_HOST, "上传") == UPLOAD_HOST


def test_guard_fixed_url_only_allows_dashscope_host():
    with pytest.raises(TranscribeError):
        _guard_fixed_url("https://evil.com/api")
    with pytest.raises(TranscribeError):
        _guard_fixed_url("http://dashscope.aliyuncs.com/api")
    assert _guard_fixed_url(ds.TASKS_URL + "/t-1") == ds.TASKS_URL + "/t-1"


def test_assert_public_host_blocks_private_resolution(monkeypatch):
    # 解析到私网地址：拦下（用真实实现，只桩 DNS 解析本身）
    monkeypatch.setattr(
        ds.socket, "getaddrinfo",
        lambda host, port, proto=None: [(socket.AF_INET, 1, 6, "", ("192.168.1.1", port))],
    )
    with pytest.raises(TranscribeError) as excinfo:
        _REAL_ASSERT_PUBLIC_HOST("dashscope.aliyuncs.com")
    assert "内网" in str(excinfo.value)

    # DNS 解析失败：报明确的错误而不是裸异常
    def no_dns(host, port, proto=None):
        raise OSError("name resolution failed")

    monkeypatch.setattr(ds.socket, "getaddrinfo", no_dns)
    with pytest.raises(TranscribeError) as excinfo:
        _REAL_ASSERT_PUBLIC_HOST("dashscope.aliyuncs.com")
    assert "无法解析" in str(excinfo.value)

    # 解析到公网地址：放行
    monkeypatch.setattr(
        ds.socket, "getaddrinfo",
        lambda host, port, proto=None: [(socket.AF_INET, 1, 6, "", ("8.141.1.1", port))],
    )
    _REAL_ASSERT_PUBLIC_HOST("dashscope.aliyuncs.com")

import pytest
import requests

from lyrics_extractor import online
from lyrics_extractor.online import OnlineError, fetch_lyrics


class FakeResp:
    def __init__(self, status_code=200, payload=None):
        self.status_code = status_code
        self._payload = payload

    def json(self):
        return self._payload


BUSY_503 = {
    "message": "The server is busy, please retry in a moment",
    "name": "ServerOverloaded",
    "statusCode": 503,
}


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    """重试退避不该让测试真的等；顺便记录每次退避时长供断言。"""
    slept = []
    monkeypatch.setattr(online.time, "sleep", slept.append)
    return slept


@pytest.fixture
def queued_api(monkeypatch):
    """按顺序返回预设响应；某路径的响应只剩最后一个时会被重复使用。"""
    calls = []
    queues = {}

    def _push(path, *responses):
        queues.setdefault(path, []).extend(responses)

    def _get(url, params=None, timeout=None, headers=None):
        path = url.rsplit("/", 1)[-1]
        calls.append({"path": path, "params": params, "headers": headers})
        queue = queues.get(path)
        if not queue:
            raise AssertionError(f"未预期的请求路径: {path}")
        return queue.pop(0) if len(queue) > 1 else queue[0]

    monkeypatch.setattr(online.requests, "get", _get)
    return calls, _push


@pytest.fixture
def fake_api(monkeypatch):
    """按接口路径（get/search）返回预设响应，并记录每次请求的参数与请求头。"""
    calls = []
    responses = {}

    def _set(path, status_code=200, payload=None):
        responses[path] = FakeResp(status_code, payload)

    def _get(url, params=None, timeout=None, headers=None):
        path = url.rsplit("/", 1)[-1]
        calls.append({"path": path, "params": params, "headers": headers})
        if path not in responses:
            raise AssertionError(f"未预期的请求路径: {path}")
        return responses[path]

    monkeypatch.setattr(online.requests, "get", _get)
    return calls, _set


RESULT_SYNCED = {
    "trackName": "晴天",
    "artistName": "周杰伦",
    "duration": 269.3,
    "instrumental": False,
    "plainLyrics": None,
    "syncedLyrics": "[00:12.34]故事的小黄花",
}


def test_get_exact_hit(fake_api):
    calls, setter = fake_api
    setter("get", 200, RESULT_SYNCED)
    result = fetch_lyrics("晴天", "周杰伦", album="叶惠美", duration=269.3)
    assert result.synced_text == "[00:12.34]故事的小黄花"
    call = calls[0]
    assert call["path"] == "get"
    assert call["params"]["track_name"] == "晴天"
    assert call["params"]["album_name"] == "叶惠美"
    assert call["params"]["duration"] == "269"
    assert "LyricsExtractor/" in call["headers"]["User-Agent"]


def test_get_404_falls_back_to_search(fake_api):
    calls, setter = fake_api
    setter("get", 404)
    setter("search", 200, [RESULT_SYNCED])
    result = fetch_lyrics("晴天", "周杰伦", duration=269.3)
    assert result.synced_text is not None
    assert [c["path"] for c in calls] == ["get", "search"]
    assert calls[1]["params"]["q"] == "周杰伦 晴天"


def test_search_prefers_synced_and_exact_title(fake_api):
    _, setter = fake_api
    setter("get", 404)
    setter(
        "search",
        200,
        [
            {
                "trackName": "晴天 (Live)",
                "artistName": "周杰伦",
                "duration": 300.0,
                "instrumental": False,
                "plainLyrics": "live 纯文本",
                "syncedLyrics": "[00:01.00]live",
            },
            RESULT_SYNCED,
        ],
    )
    result = fetch_lyrics("晴天", "周杰伦", duration=269.3)
    assert result.synced_text == "[00:12.34]故事的小黄花"


def test_search_partial_title_requires_duration_match(fake_api):
    """标题不完全一致时，只接受时长在容差内的结果。"""
    _, setter = fake_api
    setter("get", 404)
    setter(
        "search",
        200,
        [
            {
                "trackName": "晴天Live版",
                "artistName": "周杰伦",
                "duration": 400.0,  # 与 269.3 相差太远，应被排除
                "instrumental": False,
                "plainLyrics": "文本",
                "syncedLyrics": None,
            },
            {
                "trackName": "晴天Remix",
                "artistName": "周杰伦",
                "duration": 270.0,  # 容差内，可接受
                "instrumental": False,
                "plainLyrics": "remix 纯文本",
                "syncedLyrics": None,
            },
        ],
    )
    result = fetch_lyrics("晴天", "周杰伦", duration=269.3)
    assert result.plain_text == "remix 纯文本"


def test_search_all_irrelevant_returns_none(fake_api):
    _, setter = fake_api
    setter("get", 404)
    setter(
        "search",
        200,
        [
            {
                "trackName": "完全无关的歌",
                "artistName": "别人",
                "duration": 100.0,
                "instrumental": False,
                "plainLyrics": "x",
                "syncedLyrics": None,
            }
        ],
    )
    assert fetch_lyrics("晴天", "周杰伦", duration=269.3) is None


def test_search_empty_results_returns_none(fake_api):
    _, setter = fake_api
    setter("get", 404)
    setter("search", 200, [])
    assert fetch_lyrics("晴天", "周杰伦") is None


def test_instrumental_flag(fake_api):
    _, setter = fake_api
    setter(
        "get",
        200,
        {
            "trackName": "Interlude",
            "artistName": "某人",
            "duration": 60.0,
            "instrumental": True,
            "plainLyrics": None,
            "syncedLyrics": None,
        },
    )
    result = fetch_lyrics("Interlude", "某人", duration=60.0)
    assert result.instrumental is True
    assert result.synced_text is None and result.plain_text is None


def test_network_error_raises_online_error(monkeypatch):
    def boom(url, params=None, timeout=None, headers=None):
        raise requests.ConnectionError("断网了")

    monkeypatch.setattr(online.requests, "get", boom)
    with pytest.raises(OnlineError):
        fetch_lyrics("晴天", "周杰伦")


def test_server_error_raises_online_error(monkeypatch):
    monkeypatch.setattr(
        online.requests, "get", lambda *a, **k: FakeResp(status_code=500)
    )
    with pytest.raises(OnlineError):
        fetch_lyrics("晴天", "周杰伦")


def test_bad_json_raises_online_error(monkeypatch):
    class Bad:
        status_code = 200

        def json(self):
            raise ValueError("not json")

    monkeypatch.setattr(online.requests, "get", lambda *a, **k: Bad())
    with pytest.raises(OnlineError):
        fetch_lyrics("晴天", "周杰伦")


# ----------------------------------------------------------------------
# 瞬时故障重试


def test_transient_503_on_search_is_retried(queued_api):
    """lrclib 高峰期 503 ServerOverloaded 属瞬时故障，重试后应正常拿到歌词。"""
    calls, push = queued_api
    push("get", FakeResp(404))
    push("search", FakeResp(503, BUSY_503), FakeResp(200, [RESULT_SYNCED]))
    result = fetch_lyrics("晴天", "周杰伦", duration=269.3)
    assert result.synced_text == "[00:12.34]故事的小黄花"
    assert [c["path"] for c in calls] == ["get", "search", "search"]


def test_transient_503_on_exact_lookup_is_retried(queued_api):
    calls, push = queued_api
    push("get", FakeResp(503, BUSY_503), FakeResp(200, RESULT_SYNCED))
    result = fetch_lyrics("晴天", "周杰伦")
    assert result.synced_text == "[00:12.34]故事的小黄花"
    assert [c["path"] for c in calls] == ["get", "get"]


def test_persistent_503_raises_after_all_attempts(queued_api):
    calls, push = queued_api
    push("get", FakeResp(503, BUSY_503))
    with pytest.raises(OnlineError) as excinfo:
        fetch_lyrics("晴天", "周杰伦")
    assert len(calls) == online.RETRY_ATTEMPTS
    message = str(excinfo.value)
    assert "503" in message
    assert "The server is busy" in message
    assert f"已重试 {online.RETRY_ATTEMPTS} 次" in message


def test_retry_backoff_grows_exponentially(queued_api, no_sleep):
    _, push = queued_api
    push("get", FakeResp(503, BUSY_503))
    with pytest.raises(OnlineError):
        fetch_lyrics("晴天", "周杰伦")
    assert no_sleep == [
        online.RETRY_BACKOFF * 2**i for i in range(online.RETRY_ATTEMPTS - 1)
    ]


def test_network_error_is_retried(monkeypatch, no_sleep):
    attempts = []

    def flaky(url, params=None, timeout=None, headers=None):
        attempts.append(url)
        if len(attempts) < 3:
            raise requests.Timeout("读超时")
        return FakeResp(200, RESULT_SYNCED)

    monkeypatch.setattr(online.requests, "get", flaky)
    result = fetch_lyrics("晴天", "周杰伦")
    assert result.synced_text == "[00:12.34]故事的小黄花"
    assert len(attempts) == 3


def test_persistent_network_error_reports_attempts(monkeypatch, no_sleep):
    def boom(url, params=None, timeout=None, headers=None):
        raise requests.ConnectionError("断网了")

    monkeypatch.setattr(online.requests, "get", boom)
    with pytest.raises(OnlineError) as excinfo:
        fetch_lyrics("晴天", "周杰伦")
    assert "网络请求失败" in str(excinfo.value)
    assert f"已重试 {online.RETRY_ATTEMPTS} 次" in str(excinfo.value)


def test_not_found_is_not_retried(queued_api):
    """404 是「库里没有」，不是故障，重试没有意义。"""
    calls, push = queued_api
    push("get", FakeResp(404))
    push("search", FakeResp(200, []))
    assert fetch_lyrics("晴天", "周杰伦") is None
    assert [c["path"] for c in calls] == ["get", "search"]


def test_client_error_is_not_retried(queued_api):
    calls, push = queued_api
    push("get", FakeResp(400, {"message": "Bad Request"}))
    with pytest.raises(OnlineError) as excinfo:
        fetch_lyrics("晴天", "周杰伦")
    assert len(calls) == 1
    assert "400" in str(excinfo.value)


def test_bad_json_is_not_retried(queued_api):
    class Bad:
        status_code = 200

        def json(self):
            raise ValueError("not json")

    calls, push = queued_api
    push("get", Bad())
    with pytest.raises(OnlineError):
        fetch_lyrics("晴天", "周杰伦")
    assert len(calls) == 1

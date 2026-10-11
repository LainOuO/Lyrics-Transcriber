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

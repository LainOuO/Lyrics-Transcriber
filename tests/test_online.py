import sys

import pytest
import requests

from lyrics_transcriber import online
from lyrics_transcriber.online import OnlineError, fetch_lyrics


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
    assert "LyricsTranscriber/" in call["headers"]["User-Agent"]


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
    """get 和 search 都持续 503 时才最终失败，错误信息带上服务端原因。"""
    calls, push = queued_api
    push("get", FakeResp(503, BUSY_503))
    push("search", FakeResp(503, BUSY_503))
    with pytest.raises(OnlineError) as excinfo:
        fetch_lyrics("晴天", "周杰伦")
    # get 与 search 各重试 RETRY_ATTEMPTS 次
    assert len(calls) == online.RETRY_ATTEMPTS * 2
    message = str(excinfo.value)
    assert "503" in message
    assert "The server is busy" in message
    assert f"已重试 {online.RETRY_ATTEMPTS} 次" in message


def test_retry_backoff_grows_exponentially(queued_api, no_sleep):
    _, push = queued_api
    push("get", FakeResp(503, BUSY_503))
    push("search", FakeResp(503, BUSY_503))
    with pytest.raises(OnlineError):
        fetch_lyrics("晴天", "周杰伦")
    assert no_sleep == [
        online.RETRY_BACKOFF * 2**i
        for _ in ("get", "search")
        for i in range(online.RETRY_ATTEMPTS - 1)
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
    """400 属请求问题，重试没有意义；但仍要继续尝试 search 兜底。"""
    calls, push = queued_api
    push("get", FakeResp(400, {"message": "Bad Request"}))
    push("search", FakeResp(200, []))
    with pytest.raises(OnlineError) as excinfo:
        fetch_lyrics("晴天", "周杰伦")
    assert len(calls) == 2
    assert "400" in str(excinfo.value)


def test_bad_json_is_not_retried(queued_api):
    class Bad:
        status_code = 200

        def json(self):
            raise ValueError("not json")

    calls, push = queued_api
    push("get", Bad())
    push("search", FakeResp(200, []))
    with pytest.raises(OnlineError):
        fetch_lyrics("晴天", "周杰伦")
    # get 的坏响应没有重试，直接落到 search
    assert [c["path"] for c in calls] == ["get", "search"]


# ----------------------------------------------------------------------
# /get 失败不再中断 search 兜底（回归：旧版直接抛错，整条链路断掉）

def test_get_error_does_not_block_search(queued_api):
    """/get 挂了（或请求参数不完整被 400），只要 search 能查到就还有救。"""
    calls, push = queued_api
    push("get", FakeResp(400, {"message": "Bad Request"}))
    push("search", FakeResp(200, [RESULT_SYNCED]))
    result = fetch_lyrics("晴天", "周杰伦", duration=269.3)
    assert result.synced_text == "[00:12.34]故事的小黄花"
    assert [c["path"] for c in calls] == ["get", "search"]


def test_persistent_get_failure_falls_back_to_search(queued_api):
    """/get 瞬时故障重试耗尽后，不把错误抛给用户，先让 search 试一试。"""
    calls, push = queued_api
    push("get", FakeResp(503, BUSY_503))
    push("search", FakeResp(200, [RESULT_SYNCED]))
    result = fetch_lyrics("晴天", "周杰伦", duration=269.3)
    assert result.synced_text is not None
    assert [c["path"] for c in calls] == ["get", "get", "get", "search"]


def test_get_error_raised_when_search_finds_nothing(queued_api):
    """search 也一无所获时，如实抛出 /get 的失败原因（断网/参数问题），
    而不是谎报「未找到」。"""
    _, push = queued_api
    push("get", FakeResp(400, {"message": "Bad Request"}))
    push("search", FakeResp(200, []))
    with pytest.raises(OnlineError) as excinfo:
        fetch_lyrics("晴天", "周杰伦")
    assert "400" in str(excinfo.value)


def test_missing_artist_skips_get(queued_api):
    """/get 必须带歌手；歌手为空时问了也白问（必然 400），直接走 search。"""
    calls, push = queued_api
    push("search", FakeResp(200, [RESULT_SYNCED]))
    result = fetch_lyrics("晴天", "", duration=269.3)
    assert result.synced_text is not None
    assert [c["path"] for c in calls] == ["search"]


# ----------------------------------------------------------------------
# 结果挑选：歌手一致 > 同步歌词（回归：旧版只看"结果里有没有歌手名"）

def test_artist_match_outranks_synced_lyrics(fake_api):
    """正确歌手的纯文本 优先于 错误歌手的同步歌词——错歌手的词是整篇错的。"""
    _, setter = fake_api
    setter("get", 404)
    setter(
        "search",
        200,
        [
            {  # 别的歌手、带同步歌词（旧版会选它）
                "trackName": "晴天",
                "artistName": "别的歌手",
                "duration": 269.5,
                "instrumental": False,
                "plainLyrics": "别人的词",
                "syncedLyrics": "[00:01.00]别人的词",
            },
            {  # 正确歌手、只有纯文本
                "trackName": "晴天",
                "artistName": "周杰伦",
                "duration": 269.8,
                "instrumental": False,
                "plainLyrics": "故事的小黄花",
                "syncedLyrics": None,
            },
        ],
    )
    result = fetch_lyrics("晴天", "周杰伦", duration=269.3)
    assert result.plain_text == "故事的小黄花"


# ----------------------------------------------------------------------
# 查询轮次去重：合并时必须保留更强的时长校验（回归：仅歌名轮丢了校验）

def test_dedup_keeps_duration_guard(queued_api, no_romanize):
    """歌手为空时，第一轮就退化为仅歌名查询，合并后必须保留时长校验，
    否则时长对不上的同名曲（Live/另一首）会被错误认领。"""
    calls, push = queued_api
    push("search", FakeResp(200, [WRONG_HITOHIRA]))
    assert fetch_lyrics("ひとひら", "", duration=235.75) is None
    assert searched(calls) == ["ひとひら"]


def test_search_rounds_merge_keeps_strongest_guard(no_romanize):
    rounds = online._search_rounds("ひとひら", "", 235.75)
    assert rounds == [("ひとひら", (), True)]


# ----------------------------------------------------------------------
# 多轮模糊查询（仅歌名 / 罗马字歌名）

# 日文曲目：lrclib 上只有罗马字条目，原字形查不到
HITOHIRA = {
    "trackName": "Hitohira",
    "artistName": "Hata Motohiro",
    "duration": 235.0,
    "instrumental": False,
    "plainLyrics": "ひとひら の うた",
    "syncedLyrics": "[00:01.00]ひとひら の うた",
}
# 同名同字形、时长也对得上：仅歌名那一轮可以认领
SAME_TITLE_OK = dict(HITOHIRA, trackName="ひとひら", artistName="別の人")
# 同名但时长对不上（Live/另一首同名曲），绝不该被认领
WRONG_HITOHIRA = {
    "trackName": "ひとひら",
    "artistName": "ポルノグラフィティ",
    "duration": 404.0,
    "instrumental": False,
    "plainLyrics": "别人的歌词",
    "syncedLyrics": "[00:01.00]别人的歌词",
}


def searched(calls) -> list[str]:
    return [c["params"]["q"] for c in calls if c["path"] == "search"]


@pytest.fixture
def no_romanize(monkeypatch):
    """关掉罗马字那一轮，便于单独断言前两轮的查询词。"""
    monkeypatch.setattr(online, "_romanize", lambda text: None)


def test_title_only_round_requires_duration_match(queued_api, no_romanize):
    """丢掉歌手锚点后，仅歌名这一轮必须时长对得上，否则宁可放弃。"""
    calls, push = queued_api
    push("get", FakeResp(404))
    push("search", FakeResp(200, []), FakeResp(200, [WRONG_HITOHIRA]))
    assert fetch_lyrics("ひとひら", "秦基博", duration=235.75) is None
    assert searched(calls) == ["秦基博 ひとひら", "ひとひら"]


def test_title_only_round_accepts_duration_match(queued_api, no_romanize):
    calls, push = queued_api
    push("get", FakeResp(404))
    push("search", FakeResp(200, []), FakeResp(200, [WRONG_HITOHIRA, SAME_TITLE_OK]))
    result = fetch_lyrics("ひとひら", "秦基博", duration=235.75)
    assert result.synced_text == "[00:01.00]ひとひら の うた"
    assert searched(calls) == ["秦基博 ひとひら", "ひとひら"]


def test_romanized_round_finds_kanji_title(queued_api, monkeypatch):
    """前两轮都空时，用罗马字歌名再查一轮（lrclib 上的日文曲目常是罗马字条目）。"""
    monkeypatch.setattr(online, "_romanize", lambda text: "hitohira")
    calls, push = queued_api
    push("get", FakeResp(404))
    push("search", FakeResp(200, []), FakeResp(200, []), FakeResp(200, [HITOHIRA]))
    result = fetch_lyrics("ひとひら", "秦基博", duration=235.75)
    assert result.synced_text == "[00:01.00]ひとひら の うた"
    assert searched(calls) == ["秦基博 ひとひら", "ひとひら", "hitohira"]


def test_romanized_round_ignores_unrelated_same_title(queued_api, monkeypatch):
    monkeypatch.setattr(online, "_romanize", lambda text: "hitohira")
    _, push = queued_api
    push("get", FakeResp(404))
    push("search", FakeResp(200, []), FakeResp(200, []), FakeResp(200, [WRONG_HITOHIRA]))
    assert fetch_lyrics("ひとひら", "秦基博", duration=235.75) is None


def test_ascii_title_skips_extra_rounds(queued_api):
    """纯英文歌名第一轮就够用，不必多打两次请求。"""
    calls, push = queued_api
    push("get", FakeResp(404))
    push("search", FakeResp(200, []))
    assert fetch_lyrics("Lemon", "米津玄師", duration=255.0) is None
    assert len(calls) == 2


def test_missing_duration_skips_extra_rounds(queued_api):
    """本地时长未知时无法把关，不做仅歌名/罗马字这两轮。"""
    calls, push = queued_api
    push("get", FakeResp(404))
    push("search", FakeResp(200, []))
    assert fetch_lyrics("ひとひら", "秦基博") is None
    assert len(calls) == 2


def test_search_rounds_are_deduplicated(no_romanize):
    """歌手为空时第一轮查询词就等于歌名，不该重复问同一个问题。"""
    rounds = online._search_rounds("晴天", "", 269.3)
    assert [r[0] for r in rounds] == ["晴天"]


def test_search_rounds_include_romanized_title():
    """装了 pykakasi 时，非 ASCII 歌名会多出一轮罗马字查询（需要时长才能把关）。"""
    rounds = online._search_rounds("ひとひら", "秦基博", 235.75)
    assert [r[0] for r in rounds] == ["秦基博 ひとひら", "ひとひら", "hitohira"]
    assert [r[2] for r in rounds] == [False, True, True]


def test_romanize_without_pykakasi_returns_none(monkeypatch):
    monkeypatch.setitem(sys.modules, "pykakasi", None)  # 让 import pykakasi 抛 ImportError
    monkeypatch.setattr(online, "_ROMANIZER", None)
    monkeypatch.setattr(online, "_ROMANIZER_MISSING", False)
    assert online._romanize("ひとひら") is None
    assert online._ROMANIZER_MISSING is True


def test_romanize_uses_pykakasi_when_available(monkeypatch):
    pytest.importorskip("pykakasi")
    monkeypatch.setattr(online, "_ROMANIZER", None)
    monkeypatch.setattr(online, "_ROMANIZER_MISSING", False)
    assert online._romanize("ひとひら") == "hitohira"
    # 已经是罗马字的歌名不该再"转换"出个不同的东西
    assert online._romanize("Lemon") is None

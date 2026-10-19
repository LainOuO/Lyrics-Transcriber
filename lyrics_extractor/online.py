"""lrclib.net 在线歌词客户端（免费公开接口，无需鉴权）。"""

from __future__ import annotations

import time
from dataclasses import dataclass

import requests

from lyrics_extractor import __version__

LRCLIB_BASE = "https://lrclib.net/api"
USER_AGENT = f"LyricsExtractor/{__version__} (personal lyrics tool)"
DEFAULT_TIMEOUT = 10.0
# 搜索结果与本地文件时长的容差（秒），用于排除 Live/Remix 等错误版本
DURATION_TOLERANCE = 3.0
# lrclib 高峰期会返回 503（ServerOverloaded: "The server is busy, please retry in a
# moment"），纯属瞬时故障；429 与其余 5xx 同理。这类响应重试几次即可，不该直接
# 判定为「处理失败」。
RETRY_ATTEMPTS = 3  # 单个请求的总尝试次数（含首次）
RETRY_BACKOFF = 0.8  # 首次退避秒数，之后按 2 倍递增
RETRYABLE_STATUS = frozenset({408, 425, 429, 500, 502, 503, 504})


class OnlineError(Exception):
    """网络请求失败（超时、断网、服务端异常等）。"""


@dataclass
class OnlineLyrics:
    synced_text: str | None  # LRC 格式的同步歌词
    plain_text: str | None
    instrumental: bool = False


def _server_hint(resp) -> str:
    """尽量摘出服务端给出的原因（如 503 的 "The server is busy..."），取不到就留空。"""
    try:
        data = resp.json()
    except ValueError:
        return ""
    if not isinstance(data, dict):
        return ""
    hint = data.get("message") or data.get("name")
    return f"（{hint}）" if hint else ""


def _get(path: str, params: dict, timeout: float):
    """请求 lrclib；对瞬时故障（网络异常 / 429 / 5xx）退避重试后再判定失败。"""
    url = f"{LRCLIB_BASE}/{path}"
    failure: OnlineError | None = None
    for attempt in range(1, RETRY_ATTEMPTS + 1):
        if attempt > 1:
            time.sleep(RETRY_BACKOFF * 2 ** (attempt - 2))
        try:
            resp = requests.get(
                url,
                params=params,
                timeout=timeout,
                headers={"User-Agent": USER_AGENT},
            )
        except requests.RequestException as exc:
            failure = OnlineError(f"网络请求失败: {exc}")
            continue
        if resp.status_code == 404:
            return None
        if resp.status_code in RETRYABLE_STATUS:
            failure = OnlineError(f"lrclib 返回 {resp.status_code}{_server_hint(resp)}")
            continue
        if resp.status_code >= 400:
            # 其余 4xx 多为请求本身有问题，重试没有意义
            raise OnlineError(f"lrclib 返回 {resp.status_code}{_server_hint(resp)}")
        try:
            return resp.json()
        except ValueError as exc:
            raise OnlineError("lrclib 返回了无法解析的内容") from exc

    raise OnlineError(f"{failure}（已重试 {RETRY_ATTEMPTS} 次）") from failure


def fetch_lyrics(
    title: str,
    artist: str,
    album: str = "",
    duration: float | None = None,
    timeout: float = DEFAULT_TIMEOUT,
) -> OnlineLyrics | None:
    """先精确匹配 /get，失败再按多轮模糊查询；都没有则返回 None。

    /get 出错（缺歌手参数时的 400、瞬时故障重试耗尽等）不应中断流程，继续尝试
    多轮 /search；仅当 search 也一无所获时才把 /get 的错误抛出——查询本身有毛病
    时，谎报「未找到」比如实报错更糟。
    """
    params = {"track_name": title, "artist_name": artist}
    if album:
        params["album_name"] = album
    if duration:
        params["duration"] = str(int(round(duration)))

    get_error: OnlineError | None = None
    exact = None
    if artist:
        # /get 必须提供歌手；没有歌手时该接口不可能命中，问了也是白问
        try:
            exact = _get("get", params, timeout)
        except OnlineError as exc:
            get_error = exc
    if exact:
        return _to_lyrics(exact)

    search_error: OnlineError | None = None
    for query, aliases, require_duration in _search_rounds(title, artist, duration):
        try:
            results = _get("search", {"q": query}, timeout)
        except OnlineError as exc:
            search_error = exc
            continue
        if not results:
            continue
        best = _pick_best(
            results, title, duration,
            artist=artist, aliases=aliases, require_duration=require_duration,
        )
        if best is not None:
            return _to_lyrics(best)
    if get_error is not None:
        raise get_error
    if search_error is not None:
        raise search_error
    return None


def _search_rounds(title: str, artist: str, duration: float | None) -> list[tuple]:
    """多轮模糊查询的候选词，按「越精确越先试」排序，返回 (查询词, 别名, 是否强制时长)。

    第一轮「歌手 + 歌名」最精确；日文曲目在 lrclib 上常常只有罗马字条目
    （「ひとひら」→「Hitohira」），汉字/假名歌名查不到时，仅歌名、罗马字歌名
    这两轮往往能兜住。

    后两轮丢掉了歌手这个锚点，因此强制要求时长落在容差内；本地时长未知时直接
    不做这两轮——宁可查不到，也不要把别人的歌词写到文件里。

    同一个查询词出现多次时（如歌手为空，第一轮就退化为仅歌名），合并为一轮，
    但必须保留其中更强的把关要求——否则仅歌名的查询会丢掉时长校验。
    """
    rounds: list[tuple] = []
    index: dict[str, int] = {}

    def add(query: str, aliases: tuple = (), require_duration: bool = False):
        query = query.strip()
        if not query:
            return
        key = _norm(query)
        if key in index:
            position = index[key]
            old_query, old_aliases, old_require = rounds[position]
            merged_aliases = tuple(dict.fromkeys(old_aliases + tuple(aliases)))
            rounds[position] = (
                old_query,
                merged_aliases,
                old_require or require_duration,
            )
            return
        index[key] = len(rounds)
        rounds.append((query, tuple(aliases), require_duration))

    add(f"{artist} {title}".strip())
    if duration and not title.isascii():
        # 非 ASCII 歌名才需要这两轮；纯英文歌名第一轮已经够用，省一次请求
        add(title, require_duration=True)
        romanized = _romanize(title)
        if romanized:
            add(romanized, aliases=(romanized,), require_duration=True)
    return rounds


_ROMANIZER: object | None = None
_ROMANIZER_MISSING = False


def _romanize(text: str) -> str | None:
    """把日文假名/汉字转成罗马字，供查询用；没装 pykakasi 时返回 None（跳过该轮）。

    汉字读音依赖 pykakasi 的词典，人名等专有名词可能读错（如「秦基博」→
    "shinmotohiro"）；但这一轮只用来"多试一个关键词"，最终仍由歌名 + 时长容差
    把关，读错最多是多一次无结果的请求。
    """
    global _ROMANIZER, _ROMANIZER_MISSING
    if _ROMANIZER_MISSING:
        return None
    if _ROMANIZER is None:
        try:
            import pykakasi

            _ROMANIZER = pykakasi.kakasi()
        except Exception:
            # 没装 pykakasi（或打包时漏带了它的词典数据）就安静跳过这一轮：
            # 罗马字兜底只是锦上添花，不该拖垮整个查询。
            _ROMANIZER_MISSING = True
            return None
    try:
        converted = "".join(item["hepburn"] for item in _ROMANIZER.convert(text))
    except Exception:
        return None
    converted = " ".join(converted.split())
    return converted if converted and _norm(converted) != _norm(text) else None


def _norm(text: str) -> str:
    return " ".join(str(text).casefold().split())


def _within_tolerance(item: dict, duration: float | None) -> bool:
    """结果自带时长且与本地时长相差不超过容差；本地时长未知或结果无时长都不算数。"""
    if not duration or not item.get("duration"):
        return False
    return abs(float(item["duration"]) - duration) <= DURATION_TOLERANCE


def _pick_best(
    results: list,
    title: str,
    duration: float | None,
    *,
    artist: str = "",
    aliases: tuple = (),
    require_duration: bool = False,
) -> dict | None:
    """要求标题至少能对上，优先歌手一致、同步歌词、时长接近的结果。

    ``require_duration=True`` 用于丢了歌手锚点的那几轮：标题对上还不够，时长也必须
    落在容差内，才敢认领。
    """
    targets = {_norm(t) for t in (title, *aliases) if _norm(t)}
    exact = [r for r in results if _norm(r.get("trackName", "")) in targets]
    if require_duration:
        exact = [r for r in exact if _within_tolerance(r, duration)]
    candidates = exact
    if not candidates and duration:
        # 标题不完全一致时，要求时长在容差内才敢认领，避免拿到错误版本
        candidates = [
            r
            for r in results
            if _within_tolerance(r, duration)
            and any(
                target in _norm(r.get("trackName", ""))
                or _norm(r.get("trackName", "")) in target
                for target in targets
            )
        ]
    if not candidates:
        return None

    local_artist = _norm(artist)

    def score(r: dict) -> tuple:
        # 歌手一致比"有没有同步歌词"更重要：错误歌手的同步歌词是整篇错词，
        # 正确歌手的纯文本至少内容是对的（后续降级输出）。
        artist_hit = (
            1 if local_artist and local_artist in _norm(r.get("artistName", "")) else 0
        )
        has_synced = bool(r.get("syncedLyrics"))
        near = 1 if _within_tolerance(r, duration) else 0
        return (artist_hit, has_synced, near, float(r.get("duration") or 0))

    return max(candidates, key=score)


def _to_lyrics(data: dict) -> OnlineLyrics:
    synced = data.get("syncedLyrics") or None
    plain = data.get("plainLyrics") or None
    instrumental = bool(data.get("instrumental")) and not synced and not plain
    return OnlineLyrics(synced_text=synced, plain_text=plain, instrumental=instrumental)

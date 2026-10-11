"""lrclib.net 在线歌词客户端（免费公开接口，无需鉴权）。"""

from __future__ import annotations

from dataclasses import dataclass

import requests

from lyrics_extractor import __version__

LRCLIB_BASE = "https://lrclib.net/api"
USER_AGENT = f"LyricsExtractor/{__version__} (personal lyrics tool)"
DEFAULT_TIMEOUT = 10.0
# 搜索结果与本地文件时长的容差（秒），用于排除 Live/Remix 等错误版本
DURATION_TOLERANCE = 3.0


class OnlineError(Exception):
    """网络请求失败（超时、断网、服务端异常等）。"""


@dataclass
class OnlineLyrics:
    synced_text: str | None  # LRC 格式的同步歌词
    plain_text: str | None
    instrumental: bool = False


def _get(path: str, params: dict, timeout: float):
    try:
        resp = requests.get(
            f"{LRCLIB_BASE}/{path}",
            params=params,
            timeout=timeout,
            headers={"User-Agent": USER_AGENT},
        )
    except requests.RequestException as exc:
        raise OnlineError(f"网络请求失败: {exc}") from exc
    if resp.status_code == 404:
        return None
    if resp.status_code >= 400:
        raise OnlineError(f"lrclib 返回 {resp.status_code}")
    try:
        return resp.json()
    except ValueError as exc:
        raise OnlineError("lrclib 返回了无法解析的内容") from exc


def fetch_lyrics(
    title: str,
    artist: str,
    album: str = "",
    duration: float | None = None,
    timeout: float = DEFAULT_TIMEOUT,
) -> OnlineLyrics | None:
    """先精确匹配 /get，失败再走 /search 模糊匹配；都没有则返回 None。"""
    params = {"track_name": title, "artist_name": artist}
    if album:
        params["album_name"] = album
    if duration:
        params["duration"] = str(int(round(duration)))
    exact = _get("get", params, timeout)
    if exact:
        return _to_lyrics(exact)

    query = f"{artist} {title}".strip()
    results = _get("search", {"q": query}, timeout)
    if results:
        best = _pick_best(results, title, duration)
        if best is not None:
            return _to_lyrics(best)
    return None


def _norm(text: str) -> str:
    return " ".join(str(text).casefold().split())


def _pick_best(results: list, title: str, duration: float | None) -> dict | None:
    """要求标题至少能对上，优先同步歌词、歌手匹配、时长接近的结果。"""
    target = _norm(title)
    exact = [r for r in results if _norm(r.get("trackName", "")) == target]
    candidates = exact
    if not candidates and duration:
        # 标题不完全一致时，要求时长在容差内才敢认领，避免拿到错误版本
        candidates = [
            r
            for r in results
            if r.get("duration")
            and abs(float(r["duration"]) - duration) <= DURATION_TOLERANCE
            and (target in _norm(r.get("trackName", ""))
                 or _norm(r.get("trackName", "")) in target)
        ]
    if not candidates:
        return None

    def score(r: dict) -> tuple:
        has_synced = bool(r.get("syncedLyrics"))
        artist_hit = 1 if r.get("artistName") else 0
        near = 0
        if duration and r.get("duration"):
            near = 1 if abs(float(r["duration"]) - duration) <= DURATION_TOLERANCE else 0
        return (has_synced, artist_hit, near, float(r.get("duration") or 0))

    return max(candidates, key=score)


def _to_lyrics(data: dict) -> OnlineLyrics:
    synced = data.get("syncedLyrics") or None
    plain = data.get("plainLyrics") or None
    instrumental = bool(data.get("instrumental")) and not synced and not plain
    return OnlineLyrics(synced_text=synced, plain_text=plain, instrumental=instrumental)

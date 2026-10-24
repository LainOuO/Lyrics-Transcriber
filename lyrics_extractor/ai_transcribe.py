"""AI 听歌转写：内嵌与在线都拿不到歌词时，把音频交给语音识别服务兜底。

- 云端引擎走 OpenAI 兼容的 ``/v1/audio/transcriptions`` 协议（multipart 传文件 +
  Bearer 密钥），默认指向硅基流动的免费模型；任何兼容服务商（Groq、OpenAI、
  自建 whisper 服务器等）都能通过改「接口地址 / 密钥 / 模型」接入。
- 本地引擎用 whisper-timestamped（可选依赖，词级时间戳，离线可用）。
- 人声分离（Demucs，可选依赖）作为共享增强：先把人声从伴奏里剥出来再转写，
  没装依赖自动跳过。
- 服务商返回带时间戳的分段 / 词级结果时拼出同步歌词行；只有纯文本时降级为
  纯文本歌词——与 lrclib 无同步版时的降级逻辑一致。
"""

from __future__ import annotations

import mimetypes
import re
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path

import requests

from lyrics_extractor.settings import (
    ENGINE_LOCAL,
    PROVIDER_DASHSCOPE,
    Settings,
    clean_api_key,
)

DEFAULT_TIMEOUT = 180.0  # 上传 + 转写一首歌，远慢于普通接口请求
# OpenAI 兼容转写接口的通行单文件上限；超限时直接拦下，不浪费一次上传
MAX_UPLOAD_BYTES = 25 * 1024 * 1024
RETRY_ATTEMPTS = 3
RETRY_BACKOFF = 1.5
RETRYABLE_STATUS = frozenset({408, 425, 429, 500, 502, 503, 504})

# 拼行参数：把词级 / 分段结果聚成「一行歌词」
PHRASE_GAP_MS = 700  # 词间停顿超过它就断行（乐句之间的间隙）
MAX_LINE_WEIGHT = 30  # 行宽上限：中日韩字符算 2、其余算 1，约合 15 个汉字


class TranscribeError(Exception):
    """AI 转写失败（网络、鉴权、文件超限、依赖未安装、服务端异常等）。"""


@dataclass
class Transcription:
    text: str = ""  # 服务商返回的整段文本
    lines: list[tuple[int, str]] | None = None  # [(毫秒, 文本)]，无时间轴时 None
    plain_lines: list[str] = field(default_factory=list)

    @property
    def empty(self) -> bool:
        return not self.lines and not self.plain_lines


# ----------------------------------------------------------------------
# 转写结果 → LRC 歌词行


def _to_ms(value) -> int:
    """秒 → 毫秒；取不到有效数字时按 0 处理。"""
    try:
        return int(round(float(value) * 1000))
    except (TypeError, ValueError):
        return 0


def _weight(text: str) -> int:
    """行宽：中日韩及全角字符算 2，其余算 1。"""
    return sum(
        2 if ("\u2e80" <= c <= "\u9fff" or "\u3040" <= c <= "\u30ff"
              or "\uff00" <= c <= "\uffef") else 1
        for c in text
    )


_SENTENCE_END = "。！？!?…；;"


def _ends_sentence(token: str) -> bool:
    return bool(token) and token[-1] in _SENTENCE_END


def _is_punct_only(token: str) -> bool:
    return bool(token) and all(
        c in _SENTENCE_END or c in "，,、（()） \u3000" for c in token
    )


def _drop_consecutive_duplicates(
    lines: list[tuple[int, str]],
) -> list[tuple[int, str]]:
    """去掉紧挨着的重复行——Whisper 在音乐上偶发「复读机」幻觉，同一句连转好几遍。"""
    result: list[tuple[int, str]] = []
    for start, text in lines:
        if result and result[-1][1] == text:
            continue
        result.append((start, text))
    return result


def _group_words(words: list[dict]) -> list[tuple[int, str]]:
    """词级时间戳 → 歌词行。

    断行优先级：句边界（转写服务自带的句子划分，词表里以 ``sentence_start``
    标记）> 句末标点 > 乐句停顿 > 行宽上限。没有句边界标记时（部分服务不给），
    退化为后三条规则。
    """
    lines: list[tuple[int, str]] = []
    current: list[str] = []
    current_start = 0
    prev_end: int | None = None

    for word in words:
        token = str(word.get("word") or word.get("text") or "").strip()
        if not token:
            continue
        start = _to_ms(word.get("start"))
        end = _to_ms(word.get("end"))
        if not current and _is_punct_only(token):
            # 孤立的标点不该另起一行，并到上一行行尾
            if lines:
                lines[-1] = (lines[-1][0], (lines[-1][1] + token).strip())
            continue
        flush = current and (
            word.get("sentence_start")
            or _ends_sentence(current[-1])
            or (prev_end is not None and start - prev_end > PHRASE_GAP_MS)
            or _weight("".join(current)) + _weight(token) > MAX_LINE_WEIGHT
        )
        if flush:
            lines.append((current_start, "".join(current).strip()))
            current = []
        if not current:
            current_start = start
        current.append(token)
        prev_end = end
    if current:
        lines.append((current_start, "".join(current).strip()))
    return _drop_consecutive_duplicates(lines)


def _group_segments(segments: list[dict]) -> list[tuple[int, str]]:
    """分段级时间戳 → 歌词行：一段一行；段内不再切分，避免编造行内时间。"""
    lines: list[tuple[int, str]] = []
    for seg in segments:
        body = str(seg.get("text") or "").strip()
        if body:
            lines.append((_to_ms(seg.get("start")), body))
    return _drop_consecutive_duplicates(lines)


def _split_text(text: str) -> list[str]:
    """纯文本 → 歌词行：先按换行；整段没换行时按句末标点切。"""
    lines = [line.strip() for line in str(text or "").splitlines() if line.strip()]
    if len(lines) == 1:
        parts = re.split(r"(?<=[。！？!?…；;])", lines[0])
        parts = [p.strip() for p in parts if p.strip()]
        if parts:
            lines = parts
    return lines


def build_lines(words=None, segments=None, text: str = "") -> Transcription:
    """按「词级 > 分段 > 纯文本」的优先级拼出歌词行。"""
    words = [w for w in (words or []) if isinstance(w, dict)]
    segments = [s for s in (segments or []) if isinstance(s, dict)]
    if words:
        lines = _group_words(words)
        if lines:
            return Transcription(text=str(text or ""), lines=lines,
                                 plain_lines=[body for _, body in lines])
    if segments:
        lines = _group_segments(segments)
        if lines:
            return Transcription(text=str(text or ""), lines=lines,
                                 plain_lines=[body for _, body in lines])
    return Transcription(text=str(text or ""), lines=None,
                         plain_lines=_split_text(text))


# ----------------------------------------------------------------------
# 云端引擎（OpenAI 兼容 /v1/audio/transcriptions）


def transcriptions_url(base_url: str) -> str:
    """拼出转写接口地址；用户粘贴的地址带不带 ``/v1`` 都能兼容。"""
    base = str(base_url or "").strip().rstrip("/") or "https://api.siliconflow.com"
    if base.endswith("/v1"):
        return f"{base}/audio/transcriptions"
    return f"{base}/v1/audio/transcriptions"


def _server_hint(resp) -> str:
    """尽量摘出服务端给出的原因（如 {"error": {"message": ...}}），取不到留空。"""
    try:
        data = resp.json()
    except ValueError:
        return ""
    if not isinstance(data, dict):
        return ""
    error = data.get("error")
    if isinstance(error, dict) and error.get("message"):
        return f"（{error['message']}）"
    hint = data.get("message") or data.get("name")
    return f"（{hint}）" if hint else ""


# 请求阶梯：从最希望拿到的「词级时间戳」逐级降级到最朴素的纯文本请求。
# OpenAI 兼容各家实现不一：不认 response_format / timestamp_granularities 的
# 服务商会返回 400，逐级去掉参数总能落到它支持的那一级。
_REQUEST_PROFILES = (
    ("verbose_json", "word"),
    ("verbose_json", None),
    (None, None),
)


def _request_once(url, api_key, model, path: Path, timeout: float,
                  response_format, granularity):
    mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    data = {"model": model}
    if response_format:
        data["response_format"] = response_format
    if granularity:
        data["timestamp_granularities[]"] = granularity
    with open(path, "rb") as handle:
        return requests.post(
            url,
            data=data,
            files={"file": (path.name, handle, mime)},
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=timeout,
        )


def _request_with_fallback(url, api_key, model, path: Path, timeout: float):
    """按阶梯请求，返回第一个非 400 的响应（或最后一级的 400）。

    400 多为「不认这些参数」或「模型名不对」：前者降一级可救，后者三级都
    一样被拒——最终由调用方带着服务端原因抛错。
    """
    response = None
    for response_format, granularity in _REQUEST_PROFILES:
        response = _request_once(url, api_key, model, path, timeout,
                                 response_format, granularity)
        if response.status_code != 400:
            break
    return response


def _parse_payload(payload) -> Transcription:
    if not isinstance(payload, dict):
        raise TranscribeError("AI 服务返回了无法解析的内容")
    return build_lines(
        words=payload.get("words") or [],
        segments=payload.get("segments") or [],
        text=payload.get("text") or "",
    )


def transcribe_cloud(path, settings: Settings, timeout: float | None = None) -> Transcription:
    """把音频上传给云端转写接口；对瞬时故障退避重试后仍失败才抛错。"""
    path = Path(path)
    api_key = clean_api_key(getattr(settings, "api_key", ""))
    if not api_key:
        raise TranscribeError("未配置 AI 服务密钥，请先在「AI 设置」里填写")
    size = path.stat().st_size
    if size > MAX_UPLOAD_BYTES:
        raise TranscribeError(
            f"音频文件 {size / 1024 / 1024:.1f} MB，超过云端接口单文件上限"
            f" {MAX_UPLOAD_BYTES // 1024 // 1024} MB，暂不支持云端 AI 转写"
        )

    url = transcriptions_url(settings.base_url)
    timeout = timeout or DEFAULT_TIMEOUT
    failure: TranscribeError | None = None
    for attempt in range(1, RETRY_ATTEMPTS + 1):
        if attempt > 1:
            time.sleep(RETRY_BACKOFF * 2 ** (attempt - 2))
        try:
            resp = _request_with_fallback(url, api_key, settings.model,
                                          path, timeout)
            status = resp.status_code
            if status == 200:
                try:
                    payload = resp.json()
                except ValueError as exc:
                    raise TranscribeError("AI 服务返回了无法解析的内容") from exc
                return _parse_payload(payload)
            if status in RETRYABLE_STATUS:
                failure = TranscribeError(f"AI 服务返回 {status}{_server_hint(resp)}")
                continue
            if status == 401:
                raise TranscribeError(f"密钥无效（401）{_server_hint(resp)}")
            if status == 403:
                raise TranscribeError(f"无权限或模型不可用（403）{_server_hint(resp)}")
            if status == 413:
                raise TranscribeError("音频文件超过云端接口的大小上限")
            if status == 404:
                raise TranscribeError(
                    "转写接口不存在（404）：请检查「AI 设置」里的接口地址")
            raise TranscribeError(f"AI 服务返回 {status}{_server_hint(resp)}")
        except requests.RequestException as exc:
            failure = TranscribeError(f"网络请求失败: {exc}")
            continue

    raise TranscribeError(
        f"{failure}（已重试 {RETRY_ATTEMPTS} 次）") from failure


# ----------------------------------------------------------------------
# 统一入口：可选人声分离 → 按设置的引擎转写


def transcribe_audio(path, settings: Settings | None, log=None) -> Transcription:
    """AI 听歌转写主入口；``settings`` 为空时按默认云端配置（缺密钥会报错提示）。"""
    from lyrics_extractor import vocal_sep, whisper_local

    settings = settings or Settings()
    log = log or (lambda msg: None)
    source = Path(path)
    cleanup: Path | None = None
    if settings.separate_vocals:
        separated = vocal_sep.separate_vocals(path, log=log)
        if separated is not None:
            source, cleanup = separated
            log("已分离出人声轨，改用清唱音频转写")
    try:
        if settings.engine == ENGINE_LOCAL:
            return whisper_local.transcribe_local(source, settings)
        if settings.provider == PROVIDER_DASHSCOPE:
            # 懒加载：dashscope_asr 反向引用本模块的类型与拼行逻辑
            from lyrics_extractor import dashscope_asr

            return dashscope_asr.transcribe(source, settings)
        return transcribe_cloud(source, settings)
    finally:
        if cleanup is not None:
            shutil.rmtree(cleanup, ignore_errors=True)

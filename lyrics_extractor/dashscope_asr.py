"""阿里云百炼（DashScope）听歌转写适配器：本地文件也能走云端任务接口。

百炼的转写接口只认公网 URL，本地文件要先经它家的临时存储中转：

1. ``GET /api/v1/uploads?action=getPolicy`` 拿 OSS 上传凭证（与模型绑定，300s 有效）；
2. 用凭证把音频传到百炼临时存储，得到 ``oss://`` 地址（48 小时有效，≤1GB）；
3. 建转写任务（Paraformer，``X-DashScope-Async: enable``）→ 轮询任务 → 下载结果
   JSON，词级时间戳在 ``transcripts[].sentences[].words[]``（毫秒）。

安全约定：所有发往网络的地址都经 :func:`_guard_fixed_url` /
:func:`_guard_aliyun_url` 校验（https + 域名白名单 + 解析后 IP 不落在内网段），
并且禁止跟随重定向——从服务端响应里拿到的动态地址不可能把请求引去内网。
"""

from __future__ import annotations

import ipaddress
import socket
import time
import uuid
from pathlib import Path
from urllib.parse import urlparse

import requests

from lyrics_extractor.ai_transcribe import (
    Transcription,
    TranscribeError,
    build_lines,
)
from lyrics_extractor.settings import (
    DASHSCOPE_PARAFORMER_MODEL,
    Settings,
    clean_api_key,
)

DASHSCOPE_BASE = "https://dashscope.aliyuncs.com"
UPLOADS_URL = f"{DASHSCOPE_BASE}/api/v1/uploads"
TRANSCRIPTION_URL = f"{DASHSCOPE_BASE}/api/v1/services/audio/asr/transcription"
TASKS_URL = f"{DASHSCOPE_BASE}/api/v1/tasks"

# 只允许请求这些固定主机（本模块全部端点都在其上）
_ALLOWED_REQUEST_HOSTS = frozenset({"dashscope.aliyuncs.com"})

RETRY_ATTEMPTS = 3
RETRY_BACKOFF = 1.5
RETRYABLE_STATUS = frozenset({408, 425, 429, 500, 502, 503, 504})

# 任务轮询：一首歌的转写通常几十秒内完成，给足 5 分钟余量
POLL_INTERVAL = 2.0
POLL_DEADLINE = 300.0
REQUEST_TIMEOUT = 120.0


# ----------------------------------------------------------------------
# 地址防护：协议 + 域名白名单 + 解析后 IP 边界


def _assert_public_host(host: str) -> None:
    """解析主机名，任何一个解析结果落在私网/环回/链路本地段都拦下。

    防 DNS rebinding / 内网投毒：域名白名单挡不住"白名单域名被解析到内网 IP"。
    """
    try:
        infos = socket.getaddrinfo(host, 443, proto=socket.IPPROTO_TCP)
    except OSError as exc:
        raise TranscribeError(f"无法解析服务地址 {host}") from exc
    for info in infos:
        # IPv6 可能带区域后缀（fe80::1%eth0），剥掉再判
        address = info[4][0].split("%")[0]
        if not ipaddress.ip_address(address).is_global:
            raise TranscribeError(
                f"服务地址 {host} 解析到内网地址 {address}，已拦截")


def _guard_fixed_url(url: str) -> str:
    """:func:`_request` 专用：只放行 https 且主机在固定白名单内的端点。"""
    parsed = urlparse(str(url))
    host = (parsed.hostname or "").lower()
    if parsed.scheme != "https" or host not in _ALLOWED_REQUEST_HOSTS:
        raise TranscribeError("请求地址不合法")
    _assert_public_host(host)
    return url


def _guard_aliyun_url(url: str, what: str) -> str:
    """校验从百炼响应里拿到的动态地址：https 且落在 aliyuncs.com 域下。"""
    parsed = urlparse(str(url))
    host = (parsed.hostname or "").lower()
    if parsed.scheme != "https" or not host.endswith(".aliyuncs.com"):
        raise TranscribeError(f"百炼返回的{what}地址不合法")
    _assert_public_host(host)
    return url


def _headers(api_key: str, *, json_body: bool = False, oss_resolve: bool = False,
             async_task: bool = False) -> dict:
    headers = {"Authorization": f"Bearer {api_key}"}
    if json_body:
        headers["Content-Type"] = "application/json"
    if oss_resolve:
        # 传 oss:// 地址给模型接口时必须带上，否则服务端无法解析临时存储链接
        headers["X-DashScope-OssResourceResolve"] = "enable"
    if async_task:
        headers["X-DashScope-Async"] = "enable"
    return headers


def _message(payload: dict) -> str:
    """尽量摘出百炼给的原因；兼容 DashScope（code/message）与 OpenAI 风格
    （error.message 嵌套，实测兼容模式聊天接口返回这种）两种结构。"""
    if not isinstance(payload, dict):
        return ""
    error = payload.get("error")
    if isinstance(error, dict):
        payload = error
    code = payload.get("code")
    message = payload.get("message")
    if message:
        return f"（{code}: {message}）" if code else f"（{message}）"
    return ""


def _request(method: str, url: str, **kwargs):
    """带地址防护与瞬时故障退避重试的请求；固定端点专用，不接收动态地址。"""
    _guard_fixed_url(url)
    kwargs.setdefault("timeout", REQUEST_TIMEOUT)
    failure: TranscribeError | None = None
    for attempt in range(1, RETRY_ATTEMPTS + 1):
        if attempt > 1:
            time.sleep(RETRY_BACKOFF * 2 ** (attempt - 2))
        try:
            resp = requests.request(method, url, allow_redirects=False, **kwargs)
        except requests.RequestException as exc:
            failure = TranscribeError(f"网络请求失败: {exc}")
            continue
        if resp.status_code == 200:
            try:
                return resp.json()
            except ValueError as exc:
                raise TranscribeError("百炼返回了无法解析的内容") from exc
        if resp.status_code in RETRYABLE_STATUS:
            try:
                hint = _message(resp.json())
            except ValueError:
                hint = ""
            failure = TranscribeError(f"百炼返回 {resp.status_code}{hint}")
            continue
        if resp.status_code == 401:
            raise TranscribeError("密钥无效（401）")
        try:
            hint = _message(resp.json())
        except ValueError:
            hint = ""
        raise TranscribeError(f"百炼返回 {resp.status_code}{hint}")
    raise TranscribeError(
        f"{failure}（已重试 {RETRY_ATTEMPTS} 次）") from failure


# ----------------------------------------------------------------------
# 临时存储：本地文件 → oss:// 地址


def _get_policy(api_key: str, model: str) -> dict:
    payload = _request(
        "GET", UPLOADS_URL,
        params={"action": "getPolicy", "model": model},
        headers={"Authorization": f"Bearer {api_key}"},
    )
    data = payload.get("data") or {}
    if not data.get("upload_host") or not data.get("upload_dir"):
        raise TranscribeError("获取百炼上传凭证失败" + _message(payload))
    return data


def _upload_file(api_key: str, model: str, path: Path) -> str:
    """把本地文件传进百炼临时存储，返回 oss:// 地址。

    凭证禁止覆盖同名文件，文件名加一段随机前缀避免重名；key 的文件名部分
    仍保留原名，方便在百炼控制台的调用记录里认出是哪首歌。
    """
    policy = _get_policy(api_key, model)
    upload_host = _guard_aliyun_url(policy["upload_host"], "上传")
    unique_name = f"{uuid.uuid4().hex[:8]}-{path.name}"
    key = f"{policy['upload_dir']}/{unique_name}"
    with open(path, "rb") as handle:
        resp = requests.post(
            upload_host,
            files={
                "OSSAccessKeyId": (None, policy["oss_access_key_id"]),
                "Signature": (None, policy["signature"]),
                "policy": (None, policy["policy"]),
                "x-oss-object-acl": (None, policy["x_oss_object_acl"]),
                "x-oss-forbid-overwrite": (None, policy["x_oss_forbid_overwrite"]),
                "key": (None, key),
                "success_action_status": (None, "200"),
                "file": (unique_name, handle),
            },
            timeout=REQUEST_TIMEOUT,
            allow_redirects=False,
        )
    if resp.status_code != 200:
        raise TranscribeError(f"上传音频到百炼临时存储失败（{resp.status_code}）")
    return f"oss://{key}"


# ----------------------------------------------------------------------
# Paraformer：异步任务 + 词级时间戳


def _poll_task(api_key: str, task_id: str) -> str:
    """轮询任务直到完成，返回结果 JSON 的下载地址。"""
    headers = {"Authorization": f"Bearer {api_key}"}
    deadline = time.monotonic() + POLL_DEADLINE
    while True:
        payload = _request("POST", f"{TASKS_URL}/{task_id}", headers=headers)
        output = payload.get("output") or {}
        status = output.get("task_status")
        if status == "SUCCEEDED":
            results = output.get("results") or []
            url = results[0].get("transcription_url") if results else None
            if not url:
                raise TranscribeError("百炼任务完成但没给结果下载地址")
            return _guard_aliyun_url(url, "结果下载")
        if status == "FAILED":
            raise TranscribeError("百炼转写任务失败" + _message(payload))
        if time.monotonic() >= deadline:
            raise TranscribeError(f"百炼转写超时（超过 {int(POLL_DEADLINE)} 秒）")
        time.sleep(POLL_INTERVAL)


def _fetch_transcription_json(url: str) -> dict:
    """下载任务结果 JSON（预签名地址，不需要也不能带鉴权头）。"""
    try:
        resp = requests.get(
            _guard_aliyun_url(url, "结果下载"),
            timeout=REQUEST_TIMEOUT,
            allow_redirects=False,
        )
    except requests.RequestException as exc:
        raise TranscribeError(f"下载转写结果失败: {exc}") from exc
    if resp.status_code != 200:
        raise TranscribeError(f"下载转写结果失败（{resp.status_code}）")
    try:
        return resp.json()
    except ValueError as exc:
        raise TranscribeError("转写结果内容无法解析") from exc


def _to_words(sentences: list) -> list[dict]:
    """百炼 sentences → 拼行用的词列表：词文本带上随附标点，毫秒 → 秒。"""
    words: list[dict] = []
    for sentence in sentences:
        if not isinstance(sentence, dict):
            continue
        parts = sentence.get("words") or []
        if parts:
            for word in parts:
                text = str(word.get("text") or "").strip()
                if not text:
                    continue
                punct = str(word.get("punctuation") or "")
                words.append({
                    "word": text + punct,
                    "start": (word.get("begin_time") or 0) / 1000,
                    "end": (word.get("end_time") or 0) / 1000,
                })
        else:
            # 没有词级时间戳时退回句级：一句一行，行时间 = 句子开始
            text = str(sentence.get("text") or "").strip()
            if text:
                words.append({
                    "word": text,
                    "start": (sentence.get("begin_time") or 0) / 1000,
                    "end": (sentence.get("end_time") or 0) / 1000,
                })
    return words


def transcribe_paraformer(path, settings: Settings) -> Transcription:
    """Paraformer 引擎：上传 → 建任务 → 轮询 → 词级时间戳拼同步歌词行。"""
    api_key = clean_api_key(getattr(settings, "api_key", ""))
    if not api_key:
        raise TranscribeError("未配置百炼密钥，请先在「AI 设置」里填写")
    path = Path(path)
    oss_url = _upload_file(api_key, DASHSCOPE_PARAFORMER_MODEL, path)

    created = _request(
        "POST", TRANSCRIPTION_URL,
        json={
            "model": DASHSCOPE_PARAFORMER_MODEL,
            "input": {"file_urls": [oss_url]},
            # 歌词里的语气词（啊/哦）常是词的一部分，不过滤
            "parameters": {"disfluency_removal_enabled": False},
        },
        headers=_headers(api_key, json_body=True, oss_resolve=True, async_task=True),
    )
    task_id = (created.get("output") or {}).get("task_id")
    if not task_id:
        raise TranscribeError("百炼没返回任务 ID" + _message(created))

    result_url = _poll_task(api_key, task_id)
    result = _fetch_transcription_json(result_url)
    transcripts = result.get("transcripts") or []
    sentences = transcripts[0].get("sentences") if transcripts else None
    words = _to_words(sentences or [])
    text = " ".join(
        str(s.get("text") or "") for s in (sentences or []) if isinstance(s, dict)
    ).strip()
    return build_lines(words=words, text=text)


# ----------------------------------------------------------------------
# 统一入口


def transcribe(path, settings: Settings) -> Transcription:
    """百炼转写统一入口（当前引擎：Paraformer，词级时间轴）。

    Qwen 系引擎暂不提供（2026-10 实测）：qwen3-asr-flash 同步版限单条 5 分钟、
    base64 10MB，整首歌曲放不下；qwen3-asr-flash-filetrans 异步版不认临时存储
    的 oss:// 地址；qwen2-audio 不在 OpenAI 兼容模式里。百炼放开后再接入。
    """
    return transcribe_paraformer(path, settings)

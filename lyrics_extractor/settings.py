"""本机设置（AI 转写服务商等）的持久化：明文 JSON，存在用户主目录下。

密钥属于敏感信息，只写进用户自己的机器（``~/.lyrics_extractor/settings.json``），
不进代码仓库、不随软件分发。
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, fields as _fields
from pathlib import Path

# 转写引擎：云端接口或本机模型（whisper-timestamped）
ENGINE_CLOUD = "cloud"
ENGINE_LOCAL = "local"
ENGINE_LABELS = {ENGINE_CLOUD: "云端接口", ENGINE_LOCAL: "本机模型"}

# 云端服务商：阿里云百炼（国内手机号可注册，推荐）或 OpenAI 兼容（Groq 等）
PROVIDER_DASHSCOPE = "dashscope"
PROVIDER_OPENAI = "openai"
PROVIDER_LABELS = {
    PROVIDER_DASHSCOPE: "阿里云百炼（推荐，国内注册）",
    PROVIDER_OPENAI: "OpenAI 兼容（Groq 等）",
}

# 百炼的两个转写模型：Paraformer 走异步任务出词级时间戳；Qwen3-ASR 是
# LLM 系语音识别，走聊天协议只回纯文本（单条音频 ≤5 分钟）
DASHSCOPE_ENGINE_PARAFORMER = "paraformer"
DASHSCOPE_ENGINE_QWEN = "qwen"
DASHSCOPE_ENGINE_LABELS = {
    DASHSCOPE_ENGINE_PARAFORMER: "Paraformer（带时间轴，推荐）",
    DASHSCOPE_ENGINE_QWEN: "Qwen3-ASR（纯文本）",
}
DASHSCOPE_PARAFORMER_MODEL = "paraformer-v2"
DASHSCOPE_QWEN_MODEL = "qwen3-asr-flash"

SETTINGS_DIR = Path.home() / ".lyrics_extractor"
SETTINGS_PATH = SETTINGS_DIR / "settings.json"

# OpenAI 兼容服务商的默认地址与模型（Groq：免费额度按音频秒数计，支持词级
# 时间戳）。注意 Groq 的控制台网页在部分网络环境下无法访问，国内用户建议
# 选阿里云百炼。硅基流动 2026 年已下架全部转写模型，不再可用。
DEFAULT_BASE_URL = "https://api.groq.com/openai/v1"
DEFAULT_MODEL = "whisper-large-v3-turbo"
# 本机 whisper 型号：tiny / base / small / medium / large-v3
DEFAULT_LOCAL_MODEL = "small"


@dataclass
class Settings:
    engine: str = ENGINE_CLOUD
    provider: str = PROVIDER_DASHSCOPE  # 云端引擎下的服务商
    dashscope_engine: str = DASHSCOPE_ENGINE_PARAFORMER  # 百炼引擎切换
    base_url: str = DEFAULT_BASE_URL  # 仅 OpenAI 兼容服务商使用
    api_key: str = ""
    model: str = DEFAULT_MODEL  # 仅 OpenAI 兼容服务商使用
    local_model: str = DEFAULT_LOCAL_MODEL
    # 人声分离（Demucs）是可选增强：勾了但没装依赖时自动跳过
    separate_vocals: bool = True


def clean_api_key(raw: str) -> str:
    """从用户粘贴的密钥串里只保留令牌部分（sk- 开头的字母数字段）。

    实测会把「sk-xxx（硅基流动）」这样的注释一起粘进来——带中文的请求头
    会在 HTTP 层直接炸掉（latin-1 编不了），先剥干净再进任何请求。
    """
    match = re.match(r"[A-Za-z0-9_\-]+", str(raw or "").strip())
    return match.group(0) if match else ""


def load_settings(path=None) -> Settings:
    """读取设置；文件不存在或内容损坏时返回默认值，不抛错。"""
    path = Path(path) if path is not None else SETTINGS_PATH
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return Settings()
    if not isinstance(data, dict):
        return Settings()
    known = {f.name for f in _fields(Settings)}
    return Settings(**{k: v for k, v in data.items() if k in known})


def save_settings(settings: Settings, path=None) -> Path:
    """把设置写回 JSON（含明文密钥），返回写入路径。"""
    path = Path(path) if path is not None else SETTINGS_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(asdict(settings), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return path

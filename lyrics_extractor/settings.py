"""本机设置（AI 转写服务商等）的持久化：明文 JSON，存在用户主目录下。

密钥属于敏感信息，只写进用户自己的机器（``~/.lyrics_extractor/settings.json``），
不进代码仓库、不随软件分发。
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, fields as _fields
from pathlib import Path

# 转写引擎：云端接口（OpenAI 兼容）或本机模型（whisper-timestamped）
ENGINE_CLOUD = "cloud"
ENGINE_LOCAL = "local"
ENGINE_LABELS = {ENGINE_CLOUD: "云端接口", ENGINE_LOCAL: "本机模型"}

SETTINGS_DIR = Path.home() / ".lyrics_extractor"
SETTINGS_PATH = SETTINGS_DIR / "settings.json"

# 云端默认指向 Groq（OpenAI 兼容；whisper-large-v3-turbo 免费额度按音频秒数计，
# 支持词级时间戳）。硅基流动 2026 年已下架全部转写模型，不再作为默认。
DEFAULT_BASE_URL = "https://api.groq.com/openai/v1"
DEFAULT_MODEL = "whisper-large-v3-turbo"
# 本机 whisper 型号：tiny / base / small / medium / large-v3
DEFAULT_LOCAL_MODEL = "small"


@dataclass
class Settings:
    engine: str = ENGINE_CLOUD
    base_url: str = DEFAULT_BASE_URL
    api_key: str = ""
    model: str = DEFAULT_MODEL
    local_model: str = DEFAULT_LOCAL_MODEL
    # 人声分离（Demucs）是可选增强：勾了但没装依赖时自动跳过
    separate_vocals: bool = True


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

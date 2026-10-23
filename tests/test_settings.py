from lyrics_extractor.settings import (
    DEFAULT_BASE_URL,
    DEFAULT_MODEL,
    ENGINE_CLOUD,
    Settings,
    load_settings,
    save_settings,
)


def test_defaults_point_to_groq_free_tier():
    settings = Settings()
    assert settings.engine == ENGINE_CLOUD
    assert settings.base_url == DEFAULT_BASE_URL == "https://api.groq.com/openai/v1"
    assert settings.model == DEFAULT_MODEL == "whisper-large-v3-turbo"
    assert settings.api_key == ""
    assert settings.separate_vocals is True


def test_save_then_load_roundtrip(tmp_path):
    path = tmp_path / "settings.json"
    settings = Settings(api_key="sk-test", engine="local", local_model="base")
    save_settings(settings, path)
    loaded = load_settings(path)
    assert loaded == settings


def test_load_missing_file_returns_defaults(tmp_path):
    loaded = load_settings(tmp_path / "不存在.json")
    assert loaded == Settings()


def test_load_corrupt_file_returns_defaults(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text("这不是json{{{", encoding="utf-8")
    assert load_settings(path) == Settings()


def test_load_ignores_unknown_keys(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text('{"api_key": "k", "不认识的字段": 1}', encoding="utf-8")
    loaded = load_settings(path)
    assert loaded.api_key == "k"
    assert loaded == Settings(api_key="k")


def test_save_creates_parent_directory(tmp_path):
    path = tmp_path / "子目录" / "settings.json"
    save_settings(Settings(api_key="k"), path)
    assert path.is_file()
    assert load_settings(path).api_key == "k"

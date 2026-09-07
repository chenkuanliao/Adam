import json
import os
from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    data_dir: Path = Path("./data")
    database_url: str | None = None
    cors_origins: str = "http://localhost:3000,http://127.0.0.1:3000"
    max_upload_mb: int = 100
    opencode_api_key: str | None = None
    opencode_api_key_file: Path | None = None
    opencode_model: str = "gemini-3.8-flash"
    opencode_base_url: str = "https://opencode.ai/zen/v1"

    model_config = SettingsConfigDict(env_file=".env", env_prefix="ADAM_", extra="ignore")

    @property
    def resolved_database_url(self) -> str:
        return self.database_url or f"sqlite:///{self.data_dir / 'database' / 'adam.sqlite3'}"

    def prepare_directories(self) -> None:
        for name in ("database", "documents", "derived", "models", "backups", "tmp"):
            (self.data_dir / name).mkdir(parents=True, exist_ok=True)

    @property
    def resolved_opencode_api_key(self) -> str | None:
        if key := self.runtime_settings.get("api_keys", {}).get("zen"):
            return str(key)
        if self.opencode_api_key:
            return self.opencode_api_key
        if self.opencode_api_key_file and self.opencode_api_key_file.is_file():
            return self.opencode_api_key_file.read_text(encoding="utf-8").strip()
        return None

    @property
    def runtime_settings_path(self) -> Path:
        return self.data_dir / "settings.json"

    @property
    def runtime_settings(self) -> dict[str, str]:
        try:
            value = json.loads(self.runtime_settings_path.read_text(encoding="utf-8"))
            return value if isinstance(value, dict) else {}
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            return {}

    @property
    def resolved_opencode_model(self) -> str:
        runtime = self.runtime_settings
        provider = runtime.get("provider", "zen")
        return runtime.get("selected_models", {}).get(provider, runtime.get("model", self.opencode_model))

    def provider_api_key(self, provider: str) -> str | None:
        key = self.runtime_settings.get("api_keys", {}).get(provider)
        if key:
            return str(key)
        if provider == "zen":
            return self.resolved_opencode_api_key
        return None

    def save_runtime_settings(self, provider: str, model: str, api_keys: dict[str, str | None], favorites: dict[str, list[str]]) -> None:
        values = self.runtime_settings
        values["provider"] = provider
        values["model"] = model
        values.setdefault("selected_models", {})[provider] = model
        stored_keys = values.setdefault("api_keys", {})
        for name, key in api_keys.items():
            if name not in {"zen", "openrouter", "openai", "anthropic", "google"}:
                continue
            if key is None:
                stored_keys.pop(name, None)
            elif key.strip():
                stored_keys[name] = key.strip()
        values["favorites"] = {name: list(dict.fromkeys(models))[:50] for name, models in favorites.items()}
        temporary = self.runtime_settings_path.with_suffix(".tmp")
        temporary.write_text(json.dumps(values, indent=2) + "\n", encoding="utf-8")
        os.chmod(temporary, 0o600)
        temporary.replace(self.runtime_settings_path)

    def save_provider_api_key(self, provider: str, api_key: str | None) -> None:
        values = self.runtime_settings
        stored_keys = values.setdefault("api_keys", {})
        if api_key and api_key.strip():
            stored_keys[provider] = api_key.strip()
        else:
            stored_keys.pop(provider, None)
        temporary = self.runtime_settings_path.with_suffix(".tmp")
        temporary.write_text(json.dumps(values, indent=2) + "\n", encoding="utf-8")
        os.chmod(temporary, 0o600)
        temporary.replace(self.runtime_settings_path)


@lru_cache
def get_settings() -> Settings:
    settings = Settings()
    settings.prepare_directories()
    return settings

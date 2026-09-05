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
        if self.opencode_api_key:
            return self.opencode_api_key
        if self.opencode_api_key_file and self.opencode_api_key_file.is_file():
            return self.opencode_api_key_file.read_text(encoding="utf-8").strip()
        return None


@lru_cache
def get_settings() -> Settings:
    settings = Settings()
    settings.prepare_directories()
    return settings

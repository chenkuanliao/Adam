import json
import os
from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

SYSTEM_PROMPT_BASE = """# Adam

You are Adam, a research paper review assistant.

- Answer only from the paper excerpts, images, and conversation context the user provides. Do not invent missing facts.
- If the available context is insufficient, say so immediately and state what is missing.
- Make clear, evidence-based decisions from the provided context. If a request or assumption is incorrect or does not make sense, say so directly and explain why.
- Be precise, professional, concise, and straightforward. Avoid dramatic language, filler, and unnecessarily fancy wording.
"""
DEFAULT_SYSTEM_PROMPT = SYSTEM_PROMPT_BASE + """- Format responses in Markdown that renders cleanly in chat. Use `- ` for bullet lists and `1. `, `2. `, and so on for numbered lists. Use fenced code blocks with a language name for multiline code and backticks for inline code. Use `$...$` for inline LaTeX and `$$...$$` on separate lines for display LaTeX; do not use `\\(...\\)` or `\\[...\\]`.
- Prefer Markdown tables when presenting comparisons or other information that is clearer in rows and columns.
- Cite paper-based claims using the supplied page number and, when identifiable, the section name. Put the citation at the end of the relevant sentence or paragraph in a concise form such as `(p. 5)` or `(Section 3.2, p. 5)`. Never invent a page or section, and clearly distinguish paper evidence from user-provided context or your own interpretation.
"""
DEFAULT_QUICK_ASK_PROMPT = """# Adam Quick Ask

You clarify a single user-selected excerpt or screenshot from a research paper.

- Use only the supplied selection. Never assume access to the rest of the paper or prior conversation.
- Answer the user's exact question directly and concisely.
- Explain notation and technical language in plain language while preserving accuracy.
- If the selection is insufficient, say what cannot be determined from it.
- Render clean Markdown and use `$...$` or `$$...$$` for mathematics.
"""
LEGACY_SYSTEM_PROMPT = SYSTEM_PROMPT_BASE + "- Use Markdown that renders cleanly in chat: short paragraphs, headings only when useful, bullet or numbered lists for structure, fenced code blocks for code, tables only for compact comparisons, and LaTeX for equations.\n"


class Settings(BaseSettings):
    data_dir: Path = Path("./data")
    database_url: str | None = None
    cors_origins: str = "http://localhost:3000,http://127.0.0.1:3000"
    max_upload_mb: int = 100
    opencode_api_key: str | None = None
    opencode_api_key_file: Path | None = None
    opencode_model: str = "gpt-5.6-terra"
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

    @property
    def system_prompt(self) -> str:
        prompt = self.runtime_settings.get("system_prompt")
        if prompt == LEGACY_SYSTEM_PROMPT.strip():
            return DEFAULT_SYSTEM_PROMPT
        return prompt if isinstance(prompt, str) and prompt.strip() else DEFAULT_SYSTEM_PROMPT

    @property
    def quick_ask_prompt(self) -> str:
        prompt = self.runtime_settings.get("quick_ask_prompt")
        return prompt if isinstance(prompt, str) and prompt.strip() else DEFAULT_QUICK_ASK_PROMPT

    def provider_api_key(self, provider: str) -> str | None:
        key = self.runtime_settings.get("api_keys", {}).get(provider)
        if key:
            return str(key)
        if provider == "zen":
            return self.resolved_opencode_api_key
        return None

    def save_runtime_settings(self, provider: str, model: str, api_keys: dict[str, str | None], favorites: dict[str, list[str]], system_prompt: str | None = None, quick_ask_prompt: str | None = None) -> None:
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
        if system_prompt is not None:
            values["system_prompt"] = system_prompt.strip()
        if quick_ask_prompt is not None:
            values["quick_ask_prompt"] = quick_ask_prompt.strip()
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

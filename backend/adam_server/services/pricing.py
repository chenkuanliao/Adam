"""Model price tables and per-event cost estimates.

Rates come from LiteLLM's community price list (all models) plus models.dev's `opencode`
provider for OpenCode Zen's own prices. Both are cached under ``data/pricing`` with their fetch
time, refreshed when older than 24 hours, and kept when a refresh fails. Manual overrides from
Settings win over everything. Costs are always computed from stored token counts when read, so
new prices and overrides apply to past usage too.
"""
import asyncio
import json
import logging
import math
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

logger = logging.getLogger(__name__)

LITELLM_URL = "https://raw.githubusercontent.com/BerriAI/litellm/main/model_prices_and_context_window.json"
LITELLM_API_URL = "https://api.github.com/repos/BerriAI/litellm/contents/model_prices_and_context_window.json?ref=main"
MODELS_DEV_URL = "https://models.dev/api.json"
RATES_TTL_SECONDS = 24 * 60 * 60
MANUAL_REFRESH_FLOOR_SECONDS = 60
# After a failed automatic refresh, wait before trying again instead of retrying on every page load.
FAILURE_RETRY_SECONDS = 15 * 60

# LiteLLM key prefixes for model makers' own APIs; they win over clouds and resellers for bare names.
FIRST_PARTY_PROVIDERS = {"anthropic", "deepseek", "gemini", "meta", "mistral", "openai", "xai"}
# Adam provider id -> LiteLLM key prefixes to try before the bare model name.
LITELLM_PREFIXES = {
    "openai": ("openai",),
    "anthropic": ("anthropic",),
    "google": ("gemini",),
    "openrouter": ("openrouter",),
    "zen": (),
    # Sign in with ChatGPT: subscription-billed, priced as an OpenAI API-equivalent estimate.
    "chatgpt": ("openai",),
}
# Providers whose usage is covered by a subscription; their cost is an API-price estimate.
API_ESTIMATE_PROVIDERS = {"chatgpt"}
COST_SOURCES = ("providerReported", "modelPriced", "apiEstimate", "unpriced")


@dataclass(frozen=True)
class Rates:
    """USD per token."""
    input: float
    output: float
    cache_read: float
    cache_write: float

    @classmethod
    def per_million(cls, input: float, output: float, cache_read: float | None = None, cache_write: float | None = None) -> "Rates":
        return cls(input / 1e6, output / 1e6, (input if cache_read is None else cache_read) / 1e6,
                   (input if cache_write is None else cache_write) / 1e6)

    def as_per_million(self) -> dict[str, float]:
        return {"input": self.input * 1e6, "output": self.output * 1e6, "cache_read": self.cache_read * 1e6, "cache_write": self.cache_write * 1e6}


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value) if math.isfinite(value) and value >= 0 else None


def _bare(key: str) -> str:
    return key.rsplit("/", 1)[-1]


def parse_litellm(document: Any) -> dict[str, Rates]:
    """Every LiteLLM model with input and output rates; missing cache rates fall back to input."""
    table: dict[str, Rates] = {}
    if not isinstance(document, dict):
        return table
    for name, entry in document.items():
        if not isinstance(entry, dict) or name == "sample_spec":
            continue
        input_rate, output_rate = _number(entry.get("input_cost_per_token")), _number(entry.get("output_cost_per_token"))
        if input_rate is None or output_rate is None:
            continue  # Dropping beats understating cost.
        cache_read = _number(entry.get("cache_read_input_token_cost"))
        cache_write = _number(entry.get("cache_creation_input_token_cost"))
        key = name.strip().lower()
        if key:
            table[key] = Rates(input_rate, output_rate, input_rate if cache_read is None else cache_read,
                               input_rate if cache_write is None else cache_write)
    # Bare-name aliases ("anthropic/claude-x" -> "claude-x"), preferring first-party providers;
    # conflicting resellers at the same level leave the alias unpriced instead of guessing.
    candidates: dict[str, tuple[Rates | None, bool]] = {}
    for key, rates in table.items():
        alias = _bare(key)
        if alias == key or alias in table:
            continue
        first_party = key.split("/", 1)[0] in FIRST_PARTY_PROVIDERS
        held = candidates.get(alias)
        if held is None or (first_party and not held[1]):
            candidates[alias] = (rates, first_party)
        elif first_party == held[1] and held[0] != rates:
            candidates[alias] = (None, first_party)
    table.update({alias: rates for alias, (rates, _) in candidates.items() if rates is not None})
    return table


def parse_models_dev(document: Any, provider: str = "opencode") -> dict[str, Rates]:
    """models.dev lists USD per million tokens per provider; free models are legitimately $0."""
    table: dict[str, Rates] = {}
    models = ((document or {}).get(provider) or {}).get("models") if isinstance(document, dict) else None
    if not isinstance(models, dict):
        return table
    for model_id, entry in models.items():
        cost = entry.get("cost") if isinstance(entry, dict) else None
        if not isinstance(cost, dict):
            continue
        input_rate, output_rate = _number(cost.get("input")), _number(cost.get("output"))
        if input_rate is None or output_rate is None:
            continue
        table[model_id.strip().lower()] = Rates.per_million(input_rate, output_rate, _number(cost.get("cache_read")), _number(cost.get("cache_write")))
    return table


def parse_overrides(raw: Any) -> dict[str, Rates]:
    overrides: dict[str, Rates] = {}
    if not isinstance(raw, dict):
        return overrides
    for model, value in raw.items():
        if not isinstance(value, dict):
            continue
        input_rate, output_rate = _number(value.get("input")), _number(value.get("output"))
        if input_rate is None or output_rate is None or not str(model).strip():
            continue
        overrides[str(model).strip()] = Rates.per_million(input_rate, output_rate, _number(value.get("cache_read")), _number(value.get("cache_write")))
    return overrides


class RefreshTooSoon(Exception):
    def __init__(self, retry_after: int) -> None:
        super().__init__(f"Prices were refreshed recently. Try again in {retry_after} s.")
        self.retry_after = retry_after


@dataclass
class _Source:
    name: str
    path: Path
    rates: dict[str, Rates]
    fetched_at: float | None = None


class PriceBook:
    def __init__(self, cache_dir: Path, transport: httpx.AsyncBaseTransport | None = None, clock=time.time) -> None:
        self.cache_dir = cache_dir
        self.transport, self.clock = transport, clock
        self.litellm = _Source("litellm", cache_dir / "litellm-rates.json", {})
        self.zen = _Source("models.dev", cache_dir / "models-dev-opencode-rates.json", {})
        self.last_attempt: float | None = None
        self.last_error: str | None = None
        self._lock = asyncio.Lock()
        self._loaded = False

    # -- cache -------------------------------------------------------------------------------
    def _load(self) -> None:
        if self._loaded:
            return
        self._loaded = True
        for source in (self.litellm, self.zen):
            try:
                cached = json.loads(source.path.read_text(encoding="utf-8"))
                source.rates = {key: Rates(*value) for key, value in cached["models"].items()}
                source.fetched_at = float(cached["fetched_at"])
            except (OSError, ValueError, KeyError, TypeError):
                continue

    def _save(self, source: _Source) -> None:
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        payload = {"fetched_at": source.fetched_at, "source": source.name,
                   "models": {key: [r.input, r.output, r.cache_read, r.cache_write] for key, r in source.rates.items()}}
        temporary = source.path.with_suffix(".tmp")
        temporary.write_text(json.dumps(payload, separators=(",", ":")), encoding="utf-8")
        os.replace(temporary, source.path)

    # -- refresh -----------------------------------------------------------------------------
    async def _get_json(self, client: httpx.AsyncClient, url: str, accept: str | None = None) -> Any:
        response = await client.get(url, headers={"Accept": accept} if accept else None)
        response.raise_for_status()
        return response.json()

    async def _fetch_litellm(self, client: httpx.AsyncClient) -> dict[str, Rates]:
        try:
            rates = parse_litellm(await self._get_json(client, LITELLM_URL))
        except (httpx.HTTPError, ValueError):
            rates = {}
        if not rates:  # Fall back to the GitHub contents API with the raw media type.
            rates = parse_litellm(await self._get_json(client, LITELLM_API_URL, "application/vnd.github.raw+json"))
        if not rates:
            raise ValueError("LiteLLM price list contained no priced models")
        return rates

    async def _fetch_zen(self, client: httpx.AsyncClient) -> dict[str, Rates]:
        rates = parse_models_dev(await self._get_json(client, MODELS_DEV_URL))
        if not rates:
            raise ValueError("models.dev listed no OpenCode Zen prices")
        return rates

    def _stale(self, now: float) -> bool:
        return any(source.fetched_at is None or now - source.fetched_at >= RATES_TTL_SECONDS for source in (self.litellm, self.zen))

    async def ensure_fresh(self, force: bool = False) -> None:
        """Refresh stale tables. On failure the previously cached tables stay in use."""
        self._load()
        now = self.clock()
        if force and self.last_attempt is not None and now - self.last_attempt < MANUAL_REFRESH_FLOOR_SECONDS:
            raise RefreshTooSoon(math.ceil(MANUAL_REFRESH_FLOOR_SECONDS - (now - self.last_attempt)))
        if not force and (not self._stale(now) or (self.last_error and self.last_attempt is not None and now - self.last_attempt < FAILURE_RETRY_SECONDS)):
            return
        async with self._lock:
            now = self.clock()
            if not force and self.last_attempt is not None and now - self.last_attempt < 1:
                return  # Another request just refreshed.
            self.last_attempt = now
            errors = []
            async with httpx.AsyncClient(timeout=httpx.Timeout(20, connect=10), transport=self.transport, follow_redirects=True) as client:
                for source, fetch in ((self.litellm, self._fetch_litellm), (self.zen, self._fetch_zen)):
                    if not force and source.fetched_at is not None and now - source.fetched_at < RATES_TTL_SECONDS:
                        continue
                    try:
                        source.rates, source.fetched_at = await fetch(client), self.clock()
                        self._save(source)
                    except Exception as exc:  # Keep the last cached copy.
                        logger.warning("Could not refresh %s prices", source.name, exc_info=True)
                        errors.append(f"{source.name}: {exc.__class__.__name__}")
            self.last_error = "; ".join(errors) or None

    def status(self) -> dict:
        self._load()
        stamp = lambda value: None if value is None else time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(value))  # noqa: E731
        now = self.clock()
        return {"litellm_fetched_at": stamp(self.litellm.fetched_at), "models_dev_fetched_at": stamp(self.zen.fetched_at),
                "litellm_models": len(self.litellm.rates), "zen_models": len(self.zen.rates), "stale": self._stale(now),
                "error": self.last_error,
                "refresh_available_in": 0 if self.last_attempt is None else max(0, math.ceil(MANUAL_REFRESH_FLOOR_SECONDS - (now - self.last_attempt)))}

    # -- resolution --------------------------------------------------------------------------
    def resolve(self, provider: str, model: str, overrides: dict[str, Rates] | None = None) -> tuple[Rates | None, str | None]:
        """Rates for a call and where they came from: override, models.dev, litellm, or (None, None)."""
        self._load()
        if overrides and (rates := overrides.get(model.strip())):
            return rates, "override"
        key = model.strip().lower()
        if provider == "zen" and (rates := self.zen.rates.get(key)):
            return rates, "models.dev"
        candidates = [f"{prefix}/{key}" for prefix in LITELLM_PREFIXES.get(provider, (provider,))]
        candidates += [key, _bare(key)]
        # OpenRouter writes versions with dots ("claude-sonnet-4.5"); LiteLLM uses dashes.
        candidates += [candidate.replace(".", "-") for candidate in candidates if "." in candidate]
        for candidate in candidates:
            if rates := self.litellm.rates.get(candidate):
                return rates, "litellm"
        return None, None


@dataclass(frozen=True)
class PricedEvent:
    cost_usd: float
    cost_source: str
    # None when only a provider-reported total is known.
    category_cost: dict[str, float] | None
    cache_savings_usd: float


def price_event(rates: Rates | None, *, provider: str, uncached_input: int, cache_read: int, cache_write: int,
                output: int, reported_cost_usd: float | None, override: bool = False) -> PricedEvent:
    """Reasoning tokens are part of `output`; they are never charged again."""
    reported = None if override or reported_cost_usd is None or not math.isfinite(reported_cost_usd) else reported_cost_usd
    if rates is None:
        if reported is None:
            return PricedEvent(0.0, "unpriced", None, 0.0)
        return PricedEvent(reported, "providerReported", None, 0.0)
    categories = {"input": uncached_input * rates.input, "cache_read": cache_read * rates.cache_read,
                  "cache_write": cache_write * rates.cache_write, "output": output * rates.output}
    savings = cache_read * (rates.input - rates.cache_read)
    list_cost = sum(categories.values())
    if reported is not None:
        if list_cost <= 0:
            return PricedEvent(reported, "providerReported", None, savings)
        scale = reported / list_cost  # Keep the provider's total; split it like the list price.
        return PricedEvent(reported, "providerReported", {name: value * scale for name, value in categories.items()}, savings * scale)
    source = "apiEstimate" if provider in API_ESTIMATE_PROVIDERS else "modelPriced"
    return PricedEvent(list_cost, source, categories, savings)

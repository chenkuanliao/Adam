"""Normalize provider token usage and record it without ever affecting a chat response.

Every provider reports usage differently. They are normalized into four disjoint, billable
categories so pricing never counts a token twice:

- ``uncached_input``: input charged at the normal input rate
- ``cache_read``: input served from a prompt cache
- ``cache_write``: input written to a prompt cache (Anthropic-style cache creation)
- ``output``: generated tokens, *including* reasoning tokens

``reasoning`` is informational: it is a subset of ``output`` and is never priced separately.
"""
import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

USAGE_KINDS = ("chat", "quick_ask", "title")
USAGE_STATUSES = ("completed", "partial", "failed")


def _count(value: Any) -> int:
    return int(value) if isinstance(value, (int, float)) and not isinstance(value, bool) and value > 0 else 0


def _cost(value: Any) -> float | None:
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) and value >= 0 else None


@dataclass(frozen=True)
class TokenUsage:
    uncached_input: int = 0
    cache_read: int = 0
    cache_write: int = 0
    output: int = 0
    reasoning: int = 0
    reported_cost_usd: float | None = None

    @property
    def total(self) -> int:
        return self.uncached_input + self.cache_read + self.cache_write + self.output

    @property
    def is_empty(self) -> bool:
        return self.total == 0 and not self.reported_cost_usd


def responses_usage(usage: dict | None) -> TokenUsage | None:
    """OpenAI Responses API (`response.completed`).

    `input_tokens` INCLUDES `input_tokens_details.cached_tokens` and `.cache_write_tokens`;
    `output_tokens` INCLUDES `output_tokens_details.reasoning_tokens`.
    """
    if not isinstance(usage, dict):
        return None
    input_tokens = _count(usage.get("input_tokens"))
    details = usage.get("input_tokens_details") or {}
    cached = min(_count(details.get("cached_tokens")), input_tokens)
    written = min(_count(details.get("cache_write_tokens")), input_tokens - cached)
    output = _count(usage.get("output_tokens"))
    reasoning = min(_count((usage.get("output_tokens_details") or {}).get("reasoning_tokens")), output)
    return TokenUsage(uncached_input=input_tokens - cached - written, cache_read=cached, cache_write=written,
                      output=output, reasoning=reasoning)


def chat_completions_usage(usage: dict | None) -> TokenUsage | None:
    """OpenAI-compatible Chat Completions (OpenCode Zen, OpenRouter).

    `prompt_tokens` INCLUDES `prompt_tokens_details.cached_tokens` and, where reported,
    `prompt_tokens_details.cache_write_tokens`. OpenRouter adds `usage.cost` in credits (1 credit = 1 USD).
    """
    if not isinstance(usage, dict):
        return None
    prompt = _count(usage.get("prompt_tokens"))
    details = usage.get("prompt_tokens_details") or {}
    cached = min(_count(details.get("cached_tokens")), prompt)
    written = min(_count(details.get("cache_write_tokens")), prompt - cached)
    output = _count(usage.get("completion_tokens"))
    reasoning = min(_count((usage.get("completion_tokens_details") or {}).get("reasoning_tokens")), output)
    return TokenUsage(uncached_input=prompt - cached - written, cache_read=cached, cache_write=written,
                      output=output, reasoning=reasoning, reported_cost_usd=_cost(usage.get("cost")))


def merge_anthropic_usage(current: dict, update: dict | None) -> dict:
    """`message_start` carries input/cache counts; `message_delta` carries cumulative counts that win."""
    if isinstance(update, dict):
        current.update({key: value for key, value in update.items() if value is not None})
    return current


def anthropic_usage(usage: dict | None) -> TokenUsage | None:
    """Anthropic Messages. `input_tokens` EXCLUDES cache reads and cache writes.

    Thinking is billed as output; the final `message_delta` reports it in
    `output_tokens_details.thinking_tokens`.
    """
    if not isinstance(usage, dict) or not usage:
        return None
    output = _count(usage.get("output_tokens"))
    return TokenUsage(uncached_input=_count(usage.get("input_tokens")),
                      cache_read=_count(usage.get("cache_read_input_tokens")),
                      cache_write=_count(usage.get("cache_creation_input_tokens")),
                      output=output,
                      reasoning=min(_count((usage.get("output_tokens_details") or {}).get("thinking_tokens")), output))


def gemini_usage(metadata: dict | None) -> TokenUsage | None:
    """Google Gemini `usageMetadata`.

    `promptTokenCount` INCLUDES `cachedContentTokenCount`. `thoughtsTokenCount` is reported
    separately from `candidatesTokenCount` and billed as output, so output = candidates + thoughts.
    Tool-use prompt tokens are billed as input.
    """
    if not isinstance(metadata, dict):
        return None
    prompt = _count(metadata.get("promptTokenCount")) + _count(metadata.get("toolUsePromptTokenCount"))
    cached = min(_count(metadata.get("cachedContentTokenCount")), prompt)
    thoughts = _count(metadata.get("thoughtsTokenCount"))
    output = _count(metadata.get("candidatesTokenCount")) + thoughts
    return TokenUsage(uncached_input=prompt - cached, cache_read=cached, output=output, reasoning=thoughts)


def record_usage_event(session_factory: Callable[[], Session], *, kind: str, provider: str, model: str,
                       usage: TokenUsage | None, status: str, document_id: str | None = None,
                       conversation_id: str | None = None) -> bool:
    """Persist one usage row. Never raises: usage accounting must not break a response."""
    if usage is None or usage.is_empty:
        return False
    try:
        from ..models import UsageEvent
        with session_factory() as db:
            db.add(UsageEvent(kind=kind if kind in USAGE_KINDS else "chat", provider=provider[:32], model_id=model[:200],
                              document_id=document_id, conversation_id=conversation_id,
                              uncached_input_tokens=usage.uncached_input, cache_read_tokens=usage.cache_read,
                              cache_write_tokens=usage.cache_write, output_tokens=usage.output,
                              reasoning_tokens=usage.reasoning, provider_reported_cost_usd=usage.reported_cost_usd,
                              status=status if status in USAGE_STATUSES else "partial"))
            db.commit()
        return True
    except Exception:
        logger.warning("Could not record %s usage for %s/%s", kind, provider, model, exc_info=True)
        return False


class UsageTracker:
    """Collects the provider objects used for one request and records each one's usage once.

    Providers expose the latest normalized usage as a ``usage`` attribute, updated as soon as the
    stream reports it, so a cancelled or failed stream still records what was already billed.
    ``finish`` is synchronous so it can run inside ``finally`` blocks of cancelled generators.
    """

    def __init__(self, session_factory: Callable[[], Session], kind: str, document_id: str | None = None,
                 conversation_id: str | None = None) -> None:
        self.session_factory, self.kind = session_factory, kind
        self.document_id, self.conversation_id = document_id, conversation_id
        self._calls: list[tuple[str, str, Any]] = []

    def track(self, provider_name: str, model: str, provider: Any) -> Any:
        self._calls.append((provider_name, model, provider))
        return provider

    def finish(self, status: str) -> None:
        calls, self._calls = self._calls, []
        for provider_name, model, provider in calls:
            try:
                usage = getattr(provider, "usage", None)
            except Exception:
                usage = None
            record_usage_event(self.session_factory, kind=self.kind, provider=provider_name, model=model,
                               usage=usage if isinstance(usage, TokenUsage) else None, status=status,
                               document_id=self.document_id, conversation_id=self.conversation_id)

import asyncio
import json
import os
import tempfile
from datetime import datetime, timedelta, timezone
from functools import partial
from io import BytesIO
from pathlib import Path

import fitz
import httpx
import pytest

os.environ.setdefault("ADAM_DATA_DIR", tempfile.mkdtemp(prefix="adam-tests-"))
os.environ.pop("ADAM_OPENCODE_API_KEY", None)

from fastapi.testclient import TestClient  # noqa: E402

from adam_server import main as main_module  # noqa: E402
from adam_server.models import UsageEvent  # noqa: E402
from adam_server.services import llm, web_search  # noqa: E402
from adam_server.services import pricing  # noqa: E402
from adam_server.services.pricing import PriceBook, Rates, RefreshTooSoon, parse_litellm, parse_models_dev, parse_overrides, price_event  # noqa: E402
from adam_server.services.usage import TokenUsage, UsageTracker, anthropic_usage, chat_completions_usage, gemini_usage, record_usage_event, responses_usage  # noqa: E402
from adam_server.services.usage_summary import UsageRow, build_usage_summary, resolve_zone  # noqa: E402

client = TestClient(main_module.app)


# --------------------------------------------------------------------------------------------
# Recorded-style stream fixtures (shapes follow each provider's documented streaming events)
# --------------------------------------------------------------------------------------------

def sse_body(events: list[dict], done: bool = False) -> bytes:
    lines = [f"data: {json.dumps(event)}\n\n" for event in events]
    if done:
        lines.append("data: [DONE]\n\n")
    return "".join(lines).encode()


OPENAI_RESPONSES_STREAM = [
    {"type": "response.created", "response": {"id": "resp_1", "status": "in_progress", "usage": None}},
    {"type": "response.output_text.delta", "delta": "Attention "},
    {"type": "response.output_text.delta", "delta": "weights tokens."},
    {"type": "response.completed", "response": {"id": "resp_1", "status": "completed", "usage": {
        "input_tokens": 12000, "input_tokens_details": {"cached_tokens": 4000},
        "output_tokens": 1186, "output_tokens_details": {"reasoning_tokens": 1024}, "total_tokens": 13186}}},
]

OPENROUTER_CHAT_STREAM = [
    {"id": "gen-1", "choices": [{"index": 0, "delta": {"role": "assistant", "content": "Residual "}}], "usage": None},
    {"id": "gen-1", "choices": [{"index": 0, "delta": {"content": "paths."}, "finish_reason": "stop"}], "usage": None},
    {"id": "gen-1", "choices": [], "usage": {
        "prompt_tokens": 1000, "prompt_tokens_details": {"cached_tokens": 600, "cache_write_tokens": 100},
        "completion_tokens": 200, "completion_tokens_details": {"reasoning_tokens": 50}, "total_tokens": 1200, "cost": 0.0123}},
]

ANTHROPIC_STREAM = [
    {"type": "message_start", "message": {"id": "msg_1", "type": "message", "role": "assistant", "content": [], "usage": {
        "input_tokens": 50, "cache_creation_input_tokens": 9000, "cache_read_input_tokens": 0, "output_tokens": 1}}},
    {"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}},
    {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "LayerNorm "}},
    {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "normalizes."}},
    {"type": "content_block_stop", "index": 0},
    {"type": "message_delta", "delta": {"stop_reason": "end_turn"}, "usage": {"output_tokens": 320, "output_tokens_details": {"thinking_tokens": 120}}},
    {"type": "message_stop"},
]

GEMINI_STREAM = [
    {"candidates": [{"content": {"role": "model", "parts": [{"text": "Dropout "}]}}],
     "usageMetadata": {"promptTokenCount": 5000, "cachedContentTokenCount": 3000, "candidatesTokenCount": 2, "totalTokenCount": 5002}},
    {"candidates": [{"content": {"role": "model", "parts": [{"text": "regularizes."}]}, "finishReason": "STOP"}],
     "usageMetadata": {"promptTokenCount": 5000, "cachedContentTokenCount": 3000, "candidatesTokenCount": 400, "thoughtsTokenCount": 100, "totalTokenCount": 5500}},
]


@pytest.fixture
def provider_http(monkeypatch):
    """Route provider HTTP calls to a canned SSE body and capture the request payload."""
    captured: dict = {}

    def install(body: bytes, status: int = 200):
        def handler(request: httpx.Request) -> httpx.Response:
            captured["url"] = str(request.url)
            captured["json"] = json.loads(request.content or b"{}")
            return httpx.Response(status, content=body, headers={"content-type": "text/event-stream"})
        monkeypatch.setattr(llm.httpx, "AsyncClient", partial(httpx.AsyncClient, transport=httpx.MockTransport(handler)))
        return captured
    return install


async def collect(provider, limit: int | None = None) -> str:
    parts = []
    stream = provider.stream_answer("Question?", "", [], None, [])
    async for part in stream:
        parts.append(part)
        if limit is not None and len(parts) >= limit:
            await stream.aclose()
            break
    return "".join(parts)


def test_openai_responses_stream_usage_subtracts_cached_input(provider_http) -> None:
    provider_http(sse_body(OPENAI_RESPONSES_STREAM, done=True))
    provider = llm.OpenAIResponsesProvider("key", "gpt-5.6-terra", "https://api.openai.com/v1", "System")
    assert asyncio.run(collect(provider)) == "Attention weights tokens."
    assert provider.usage == TokenUsage(uncached_input=8000, cache_read=4000, cache_write=0, output=1186, reasoning=1024)


def test_responses_usage_cache_writes_are_part_of_input() -> None:
    usage = responses_usage({"input_tokens": 12000, "input_tokens_details": {"cached_tokens": 4000, "cache_write_tokens": 7000}, "output_tokens": 10})
    assert (usage.uncached_input, usage.cache_read, usage.cache_write) == (1000, 4000, 7000)


def test_chat_completions_stream_requests_usage_and_reads_openrouter_cost(provider_http) -> None:
    captured = provider_http(sse_body(OPENROUTER_CHAT_STREAM, done=True))
    provider = llm.OpenAICompatibleProvider("key", "anthropic/claude-sonnet-4.5", "https://openrouter.ai/api/v1", "System")
    assert asyncio.run(collect(provider)) == "Residual paths."
    assert captured["json"]["stream_options"] == {"include_usage": True}
    assert provider.usage == TokenUsage(uncached_input=300, cache_read=600, cache_write=100, output=200, reasoning=50, reported_cost_usd=0.0123)


def test_chat_completions_usage_without_details() -> None:
    usage = chat_completions_usage({"prompt_tokens": 70, "completion_tokens": 30, "total_tokens": 100})
    assert usage == TokenUsage(uncached_input=70, output=30)
    assert usage.reported_cost_usd is None


def test_anthropic_stream_combines_start_and_delta_usage_and_caches_system(provider_http) -> None:
    captured = provider_http(sse_body(ANTHROPIC_STREAM))
    provider = llm.AnthropicProvider("key", "claude-sonnet-4-5", system_prompt="Paper context " * 50)
    assert asyncio.run(collect(provider)) == "LayerNorm normalizes."
    # input_tokens excludes cache; cache creation and reads are separate categories.
    assert provider.usage == TokenUsage(uncached_input=50, cache_read=0, cache_write=9000, output=320, reasoning=120)
    system = captured["json"]["system"]
    assert system == [{"type": "text", "text": "Paper context " * 50, "cache_control": {"type": "ephemeral"}}]


def test_anthropic_cache_hit_and_cumulative_delta_overrides() -> None:
    merged: dict = {}
    llm.merge_anthropic_usage(merged, {"input_tokens": 40, "cache_read_input_tokens": 9000, "cache_creation_input_tokens": 0, "output_tokens": 1})
    llm.merge_anthropic_usage(merged, {"output_tokens": 210, "input_tokens": 42})
    assert anthropic_usage(merged) == TokenUsage(uncached_input=42, cache_read=9000, cache_write=0, output=210)


def test_anthropic_non_claude_and_empty_system_prompts_are_not_cache_blocks() -> None:
    assert llm.AnthropicProvider("k", "qwen3.8-max", "https://opencode.ai/zen/v1", "Prompt").system_blocks() == "Prompt"
    assert llm.AnthropicProvider("k", "claude-haiku-4-5", system_prompt="").system_blocks() == ""


def test_gemini_stream_uses_last_cumulative_metadata_and_counts_thoughts_as_output(provider_http) -> None:
    provider_http(sse_body(GEMINI_STREAM))
    provider = llm.GoogleProvider("key", "gemini-2.5-pro", "https://generativelanguage.googleapis.com/v1beta", "System")
    assert asyncio.run(collect(provider)) == "Dropout regularizes."
    assert provider.usage == TokenUsage(uncached_input=2000, cache_read=3000, output=500, reasoning=100)


def test_gemini_tool_use_prompt_tokens_are_input() -> None:
    assert gemini_usage({"promptTokenCount": 10, "toolUsePromptTokenCount": 5, "candidatesTokenCount": 3}) == TokenUsage(uncached_input=15, output=3)


def test_cancelled_stream_keeps_usage_reported_before_cancel(provider_http) -> None:
    provider_http(sse_body(ANTHROPIC_STREAM))
    provider = llm.AnthropicProvider("key", "claude-sonnet-4-5", system_prompt="Context")
    assert asyncio.run(collect(provider, limit=1)) == "LayerNorm "
    # message_start already reported the input side of the bill.
    assert provider.usage.cache_write == 9000 and provider.usage.uncached_input == 50


def test_malformed_usage_is_ignored() -> None:
    assert responses_usage(None) is None
    assert chat_completions_usage({"prompt_tokens": "lots", "completion_tokens": -4}) == TokenUsage()
    # Cached can never exceed the input it is part of.
    assert responses_usage({"input_tokens": 10, "input_tokens_details": {"cached_tokens": 50}}).uncached_input == 0


def test_planner_usage_is_surfaced_to_the_caller(provider_http) -> None:
    provider_http(sse_body([*OPENAI_RESPONSES_STREAM[:1], {"type": "response.output_text.delta", "delta": '{"query": "attention"}'}, OPENAI_RESPONSES_STREAM[-1]]))
    planner = llm.OpenAIResponsesProvider("key", "gpt-5.6-terra", "https://api.openai.com/v1", "")
    assert asyncio.run(web_search.plan_search(planner, "What is attention?", "", "Paper")) == "attention"
    assert planner.usage.output == 1186


def test_zen_title_usage_is_reported(monkeypatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"output_text": "Attention Heads", "usage": {"input_tokens": 300, "input_tokens_details": {"cached_tokens": 0}, "output_tokens": 6}})
    monkeypatch.setattr(llm.httpx, "AsyncClient", partial(httpx.AsyncClient, transport=httpx.MockTransport(handler)))
    seen = []
    assert asyncio.run(llm.generate_zen_title("key", "User: ...", "fallback", on_usage=seen.append)) == "Attention Heads"
    assert seen == [TokenUsage(uncached_input=300, output=6)]


# --------------------------------------------------------------------------------------------
# Pricing
# --------------------------------------------------------------------------------------------

LITELLM_DOC = {
    "sample_spec": {"input_cost_per_token": 1, "output_cost_per_token": 1},
    "gpt-5.6-terra": {"input_cost_per_token": 2e-06, "output_cost_per_token": 1.2e-05, "cache_read_input_token_cost": 2e-07, "litellm_provider": "openai"},
    "claude-sonnet-4-5": {"input_cost_per_token": 3e-06, "output_cost_per_token": 1.5e-05, "cache_read_input_token_cost": 3e-07, "cache_creation_input_token_cost": 3.75e-06},
    "gemini/gemini-2.5-pro": {"input_cost_per_token": 1.25e-06, "output_cost_per_token": 1e-05},
    "gemini-2.5-pro": {"input_cost_per_token": 9e-06, "output_cost_per_token": 9e-05},
    "openai/o-mini-x": {"input_cost_per_token": 1e-06, "output_cost_per_token": 4e-06},
    "azure/o-mini-x": {"input_cost_per_token": 5e-06, "output_cost_per_token": 5e-06},
    "reseller-a/shared-model": {"input_cost_per_token": 1e-06, "output_cost_per_token": 1e-06},
    "reseller-b/shared-model": {"input_cost_per_token": 2e-06, "output_cost_per_token": 2e-06},
    "embedding-only": {"input_cost_per_token": 1e-07},
    "openrouter/anthropic/claude-sonnet-4.5": {"input_cost_per_token": 3e-06, "output_cost_per_token": 1.5e-05},
}
MODELS_DEV_DOC = {"opencode": {"models": {
    "gpt-5": {"cost": {"input": 1.07, "output": 8.5, "cache_read": 0.107}},
    "big-pickle": {"cost": {"input": 0, "output": 0}},
    "no-price": {"name": "x"},
}}, "openai": {"models": {"gpt-5": {"cost": {"input": 1.25, "output": 10}}}}}


def book_with(tmp_path: Path, litellm_doc=LITELLM_DOC, models_dev_doc=MODELS_DEV_DOC) -> PriceBook:
    book = PriceBook(tmp_path)
    book._loaded = True
    book.litellm.rates, book.litellm.fetched_at = parse_litellm(litellm_doc), 1e12
    book.zen.rates, book.zen.fetched_at = parse_models_dev(models_dev_doc), 1e12
    return book


def test_litellm_parsing_fallbacks_and_aliases() -> None:
    table = parse_litellm(LITELLM_DOC)
    assert "sample_spec" not in table and "embedding-only" not in table
    # Missing cache rates fall back to the input rate.
    terra = table["gpt-5.6-terra"]
    assert terra.cache_read == 2e-07 and terra.cache_write == 2e-06
    # A bare alias prefers the first-party provider over a cloud reseller.
    assert table["o-mini-x"].input == 1e-06
    # Conflicting non-first-party resellers leave the alias unpriced instead of guessing.
    assert "shared-model" not in table
    # An exact bare entry is never replaced by an alias.
    assert table["gemini-2.5-pro"].input == 9e-06


def test_models_dev_parsing_per_million_and_free_models() -> None:
    table = parse_models_dev(MODELS_DEV_DOC)
    assert table["gpt-5"] == Rates.per_million(1.07, 8.5, 0.107)
    assert table["gpt-5"].cache_write == pytest.approx(1.07e-06)
    assert table["big-pickle"] == Rates(0, 0, 0, 0)
    assert "no-price" not in table


def test_model_resolution_order(tmp_path) -> None:
    book = book_with(tmp_path)
    assert book.resolve("google", "gemini-2.5-pro") == (Rates(1.25e-06, 1e-05, 1.25e-06, 1.25e-06), "litellm")
    assert book.resolve("anthropic", "claude-sonnet-4-5")[1] == "litellm"
    assert book.resolve("openrouter", "anthropic/claude-sonnet-4.5")[0].output == 1.5e-05
    # Dotted OpenRouter versions fall back to LiteLLM's dashed first-party names.
    assert book.resolve("openrouter", "anthropic/claude-sonnet-4.5", None) == book.resolve("openrouter", "anthropic/claude-sonnet-4.5")
    assert book_with(tmp_path, {k: v for k, v in LITELLM_DOC.items() if not k.startswith("openrouter/")}).resolve("openrouter", "anthropic/claude-sonnet-4.5")[0].cache_write == 3.75e-06
    # Zen uses its own published prices first, then LiteLLM by bare name.
    assert book.resolve("zen", "gpt-5") == (Rates.per_million(1.07, 8.5, 0.107), "models.dev")
    assert book.resolve("zen", "gpt-5.6-terra")[1] == "litellm"
    assert book.resolve("zen", "mystery-model") == (None, None)
    overrides = parse_overrides({"gpt-5": {"input": 9, "output": 9}})
    assert book.resolve("zen", "gpt-5", overrides) == (Rates.per_million(9, 9), "override")


def test_price_event_model_priced_and_cache_savings() -> None:
    rates = Rates.per_million(3, 15, 0.3, 3.75)
    priced = price_event(rates, provider="anthropic", uncached_input=1_000_000, cache_read=2_000_000, cache_write=1_000_000, output=100_000, reported_cost_usd=None)
    assert priced.cost_source == "modelPriced"
    assert priced.category_cost == pytest.approx({"input": 3.0, "cache_read": 0.6, "cache_write": 3.75, "output": 1.5})
    assert priced.cost_usd == pytest.approx(8.85)
    assert priced.cache_savings_usd == pytest.approx(2_000_000 * (3 - 0.3) / 1e6)


def test_reasoning_tokens_are_never_charged_twice() -> None:
    rates = Rates.per_million(1, 10)
    priced = price_event(rates, provider="openai", uncached_input=0, cache_read=0, cache_write=0, output=1_000_000, reported_cost_usd=None)
    assert priced.cost_usd == pytest.approx(10.0)


def test_provider_reported_cost_scales_category_split() -> None:
    rates = Rates.per_million(2, 8)
    priced = price_event(rates, provider="openrouter", uncached_input=1_000_000, cache_read=0, cache_write=0, output=250_000, reported_cost_usd=2.0)
    # List price is $4 (input $2 + output $2); the reported $2 keeps the same 50/50 split.
    assert priced.cost_source == "providerReported" and priced.cost_usd == 2.0
    assert priced.category_cost == pytest.approx({"input": 1.0, "cache_read": 0.0, "cache_write": 0.0, "output": 1.0})
    unpriced_but_reported = price_event(None, provider="openrouter", uncached_input=10, cache_read=0, cache_write=0, output=10, reported_cost_usd=0.5)
    assert (unpriced_but_reported.cost_source, unpriced_but_reported.cost_usd, unpriced_but_reported.category_cost) == ("providerReported", 0.5, None)


def test_override_beats_reported_cost_unpriced_and_api_estimate() -> None:
    overridden = price_event(Rates.per_million(1, 1), provider="openrouter", uncached_input=1_000_000, cache_read=0, cache_write=0, output=0, reported_cost_usd=7.0, override=True)
    assert (overridden.cost_source, overridden.cost_usd) == ("modelPriced", pytest.approx(1.0))
    unpriced = price_event(None, provider="zen", uncached_input=100, cache_read=0, cache_write=0, output=5, reported_cost_usd=None)
    assert (unpriced.cost_source, unpriced.cost_usd, unpriced.category_cost) == ("unpriced", 0.0, None)
    estimate = price_event(Rates.per_million(1, 1), provider="chatgpt", uncached_input=1_000_000, cache_read=0, cache_write=0, output=0, reported_cost_usd=None)
    assert estimate.cost_source == "apiEstimate"


def test_overrides_default_cache_rates_to_input_and_drop_invalid() -> None:
    overrides = parse_overrides({"a": {"input": 2, "output": 4, "cache_read": None}, "b": {"input": 1}, "c": "nope"})
    assert overrides == {"a": Rates.per_million(2, 4, 2, 2)}


# --------------------------------------------------------------------------------------------
# Rate cache TTL, refresh floor, and failure fallback
# --------------------------------------------------------------------------------------------

class Clock:
    def __init__(self) -> None:
        self.now = 1_000_000.0

    def __call__(self) -> float:
        return self.now


def counting_transport(state: dict) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        state.setdefault("calls", []).append(url)
        if state.get("fail") or (state.get("fail_raw") and url == pricing.LITELLM_URL):
            return httpx.Response(503)
        if url.startswith("https://api.github.com/"):
            assert request.headers["accept"] == "application/vnd.github.raw+json"
            return httpx.Response(200, json=LITELLM_DOC)
        if url == pricing.LITELLM_URL:
            return httpx.Response(200, json=LITELLM_DOC)
        if url == pricing.MODELS_DEV_URL:
            return httpx.Response(200, json=MODELS_DEV_DOC)
        return httpx.Response(404)
    return httpx.MockTransport(handler)


def test_rates_fetch_cache_ttl_and_disk_reload(tmp_path) -> None:
    state: dict = {}
    clock = Clock()
    book = PriceBook(tmp_path, transport=counting_transport(state), clock=clock)
    asyncio.run(book.ensure_fresh())
    assert len(state["calls"]) == 2 and book.resolve("zen", "gpt-5")[1] == "models.dev"
    status = book.status()
    assert status["litellm_models"] > 0 and status["stale"] is False and status["error"] is None
    clock.now += 23 * 3600
    asyncio.run(book.ensure_fresh())
    assert len(state["calls"]) == 2  # Still fresh.
    clock.now += 2 * 3600
    asyncio.run(book.ensure_fresh())
    assert len(state["calls"]) == 4  # Older than 24h: refreshed.
    # A new process loads the cached tables, including their fetch time, from ./data.
    reloaded = PriceBook(tmp_path, transport=counting_transport({"fail": True}), clock=clock)
    assert reloaded.resolve("anthropic", "claude-sonnet-4-5")[1] == "litellm"
    assert reloaded.status()["stale"] is False


def test_rates_fall_back_to_github_contents_api(tmp_path) -> None:
    state: dict = {"fail_raw": True}
    book = PriceBook(tmp_path, transport=counting_transport(state), clock=Clock())
    asyncio.run(book.ensure_fresh())
    assert any(url.startswith("https://api.github.com/") for url in state["calls"])
    assert book.resolve("openai", "gpt-5.6-terra")[0] is not None


def test_failed_refresh_keeps_cached_rates_and_backs_off(tmp_path) -> None:
    clock = Clock()
    asyncio.run(PriceBook(tmp_path, transport=counting_transport({}), clock=clock).ensure_fresh())
    clock.now += 25 * 3600
    state: dict = {"fail": True}
    book = PriceBook(tmp_path, transport=counting_transport(state), clock=clock)
    asyncio.run(book.ensure_fresh())
    assert book.resolve("anthropic", "claude-sonnet-4-5")[1] == "litellm"
    assert book.status()["error"] and book.status()["stale"]
    attempts = len(state["calls"])
    clock.now += 60
    asyncio.run(book.ensure_fresh())
    assert len(state["calls"]) == attempts  # No retry storm on every dashboard load.


def test_manual_refresh_has_a_sixty_second_floor(tmp_path) -> None:
    clock = Clock()
    book = PriceBook(tmp_path, transport=counting_transport({}), clock=clock)
    asyncio.run(book.ensure_fresh(force=True))
    clock.now += 30
    with pytest.raises(RefreshTooSoon) as error:
        asyncio.run(book.ensure_fresh(force=True))
    assert error.value.retry_after == 30
    clock.now += 31
    asyncio.run(book.ensure_fresh(force=True))


# --------------------------------------------------------------------------------------------
# Aggregation
# --------------------------------------------------------------------------------------------

NOW = datetime(2026, 11, 5, 18, 30, tzinfo=timezone.utc)


def row(hours_ago: float, provider: str, model: str, *, kind: str = "chat", uncached=1_000_000, cache_read=0, cache_write=0, output=0, cost=None) -> UsageRow:
    return UsageRow(NOW - timedelta(hours=hours_ago), kind, provider, model, uncached, cache_read, cache_write, output, 0, cost, "completed")


def test_summary_by_range_provider_model_and_day(tmp_path) -> None:
    book = book_with(tmp_path)
    rows = [
        row(1, "anthropic", "claude-sonnet-4-5", cache_read=1_000_000, output=100_000),
        row(30, "anthropic", "claude-sonnet-4-5"),
        row(50, "zen", "gpt-5", kind="title"),
        row(5, "zen", "mystery-model", output=10),
        row(24 * 10, "openai", "gpt-5.6-terra"),  # Outside a 7-day window.
    ]
    summary = build_usage_summary(rows, book=book, overrides={}, days=7, now=NOW, zone=timezone.utc)
    assert summary["range"]["resolution"] == "day" and len(summary["range"]["buckets"]) == 7
    assert summary["available_providers"] == ["anthropic", "zen"]
    totals = summary["totals"]
    assert totals["requests"] == 4 and totals["unpriced_requests"] == 1
    # Sonnet: (1M input * $3) + (1M cache read * $0.30) + (100k output * $15) = $4.80; then $3.00. Zen gpt-5: $1.07.
    assert totals["cost_usd"] == pytest.approx(4.8 + 3.0 + 1.07)
    assert totals["cache_savings_usd"] == pytest.approx(2.7)
    assert totals["cost_sources"]["unpriced"] == 1 and totals["cost_sources"]["modelPriced"] == 3
    providers = {item["provider"]: item for item in summary["providers"]}
    assert providers["anthropic"]["cost_share"] == pytest.approx(7.8 / 8.87)
    models = {item["model"]: item for item in summary["models"]}
    assert models["mystery-model"]["cost_unknown"] is True and models["mystery-model"]["unpriced_tokens"] == 1_000_010
    assert models["claude-sonnet-4-5"]["rate_source"] == "litellm"
    by_day = {item["bucket"]: item for item in summary["series"]}
    assert by_day["2026-11-05"]["cost_usd"] == pytest.approx(4.8)
    assert by_day["2026-11-04"]["by_provider"]["anthropic"]["cost_usd"] == pytest.approx(3.0)
    assert sum(models["claude-sonnet-4-5"]["series_tokens"]) == 3_100_000
    assert len(models["claude-sonnet-4-5"]["series_cost"]) == 7


def test_summary_filters_kind_and_provider_and_hourly_window(tmp_path) -> None:
    book = book_with(tmp_path)
    rows = [row(1, "anthropic", "claude-sonnet-4-5"), row(2, "zen", "gpt-5", kind="title"), row(30, "zen", "gpt-5")]
    hourly = build_usage_summary(rows, book=book, overrides={}, days=1, now=NOW, zone=timezone.utc)
    assert hourly["range"]["resolution"] == "hour" and len(hourly["series"]) == 24
    assert hourly["totals"]["requests"] == 2
    filtered = build_usage_summary(rows, book=book, overrides={}, days=7, now=NOW, zone=timezone.utc, provider="zen")
    assert filtered["available_providers"] == ["anthropic", "zen"] and filtered["totals"]["requests"] == 2
    titles = build_usage_summary(rows, book=book, overrides={}, days=7, now=NOW, zone=timezone.utc, kind="title")
    assert titles["totals"]["requests"] == 1 and titles["kinds"]["title"]["requests"] == 1


def test_summary_uses_local_days_and_overrides_apply_retroactively(tmp_path) -> None:
    book = book_with(tmp_path)
    late_evening = [UsageRow(datetime(2026, 11, 5, 2, 0, tzinfo=timezone.utc), "chat", "zen", "mystery-model", 1_000_000, 0, 0, 0, 0, None, "completed")]
    chicago = build_usage_summary(late_evening, book=book, overrides={}, days=7, now=NOW, zone=resolve_zone("America/Chicago"))
    # 02:00 UTC is the previous evening in Chicago.
    assert {item["bucket"]: item["requests"] for item in chicago["series"]}["2026-11-04"] == 1
    priced = build_usage_summary(late_evening, book=book, overrides=parse_overrides({"mystery-model": {"input": 4, "output": 8}}), days=7, now=NOW, zone=timezone.utc)
    assert priced["totals"]["cost_usd"] == pytest.approx(4.0) and priced["models"][0]["rate_source"] == "override"
    assert resolve_zone("Not/AZone") == timezone.utc


def test_reported_cost_without_rates_counts_as_other_category(tmp_path) -> None:
    book = book_with(tmp_path)
    summary = build_usage_summary([row(1, "openrouter", "unknown/model", cost=0.25)], book=book, overrides={}, days=7, now=NOW, zone=timezone.utc)
    assert summary["totals"]["cost_usd"] == 0.25 and summary["totals"]["category_cost"]["other"] == 0.25


# --------------------------------------------------------------------------------------------
# Recording through the API, and failures never breaking chat
# --------------------------------------------------------------------------------------------

def upload_paper(text: str) -> dict:
    document = fitz.open()
    document.new_page().insert_text((72, 72), text)
    pdf = document.tobytes()
    document.close()
    response = client.post("/api/documents", files={"file": (f"usage-{abs(hash(text))}.pdf", BytesIO(pdf), "application/pdf")})
    assert response.status_code == 201
    return response.json()


class UsageProvider:
    def __init__(self, fail_after_usage: bool = False) -> None:
        self.usage = None
        self.fail_after_usage = fail_after_usage

    async def stream_answer(self, question, selected_text, images, page, history):
        self.usage = TokenUsage(uncached_input=1200, cache_read=800, output=90)
        yield "An answer."
        if self.fail_after_usage:
            raise httpx.ReadError("connection dropped")


def usage_events(conversation_id: str | None = None, document_id: str | None = None) -> list[UsageEvent]:
    with main_module.SessionLocal() as db:
        query = db.query(UsageEvent)
        if conversation_id:
            query = query.filter(UsageEvent.conversation_id == conversation_id)
        if document_id:
            query = query.filter(UsageEvent.document_id == document_id)
        return query.all()


def use_usage_provider(monkeypatch, provider_factory) -> None:
    monkeypatch.setattr(type(main_module.settings), "provider_api_key", lambda _self, _provider: "test-key")
    monkeypatch.setattr(main_module, "make_provider", lambda *_args, **_kwargs: provider_factory())

    async def fake_title(_transcript, _fallback):
        return "Generated Title"
    monkeypatch.setattr(main_module, "generate_configured_title", fake_title)


def test_chat_and_quick_ask_record_usage_that_survives_deletion(monkeypatch) -> None:
    use_usage_provider(monkeypatch, UsageProvider)
    paper = upload_paper("Usage recording paper.")
    conversation = client.post(f"/api/documents/{paper['id']}/conversations", json={}).json()
    response = client.post(f"/api/conversations/{conversation['id']}/messages/stream", json={"question": "What?"})
    assert '"type": "completed"' in response.text
    client.post(f"/api/conversations/{conversation['id']}/quick-ask/stream", json={"question": "Huh?", "selected_text": "Usage"})
    events = usage_events(conversation_id=conversation["id"])
    assert sorted(event.kind for event in events) == ["chat", "quick_ask"]
    chat = next(event for event in events if event.kind == "chat")
    assert (chat.uncached_input_tokens, chat.cache_read_tokens, chat.output_tokens, chat.status, chat.document_id) == (1200, 800, 90, "completed", paper["id"])
    assert client.delete(f"/api/documents/{paper['id']}").status_code == 204
    with main_module.SessionLocal() as db:
        survivors = db.query(UsageEvent).filter(UsageEvent.id.in_([event.id for event in events])).all()
    assert len(survivors) == 2 and all(event.document_id is None and event.conversation_id is None for event in survivors)


def test_stream_error_after_usage_records_failed_event(monkeypatch) -> None:
    use_usage_provider(monkeypatch, lambda: UsageProvider(fail_after_usage=True))
    paper = upload_paper("Failing stream paper.")
    conversation = client.post(f"/api/documents/{paper['id']}/conversations", json={}).json()
    response = client.post(f"/api/conversations/{conversation['id']}/messages/stream", json={"question": "What?"})
    assert '"type": "error"' in response.text
    [event] = usage_events(conversation_id=conversation["id"])
    assert event.status == "failed" and event.uncached_input_tokens == 1200


def test_usage_recording_failure_never_breaks_chat(monkeypatch) -> None:
    use_usage_provider(monkeypatch, UsageProvider)
    paper = upload_paper("Broken usage store paper.")
    conversation = client.post(f"/api/documents/{paper['id']}/conversations", json={}).json()

    def broken_session():
        raise RuntimeError("database is locked")
    real_tracker = main_module.UsageTracker
    # Only usage accounting gets the failing database; the chat itself keeps working sessions.
    monkeypatch.setattr(main_module, "UsageTracker", lambda _factory, *args: real_tracker(broken_session, *args))
    response = client.post(f"/api/conversations/{conversation['id']}/messages/stream", json={"question": "Still works?"})
    assert '"type": "completed"' in response.text and '"type": "error"' not in response.text
    assert usage_events(conversation_id=conversation["id"]) == []
    detail = client.get(f"/api/conversations/{conversation['id']}").json()
    assert detail["messages"][-1]["content"] == "An answer."


def test_record_usage_event_skips_empty_usage_and_swallows_errors() -> None:
    assert record_usage_event(main_module.SessionLocal, kind="chat", provider="zen", model="m", usage=TokenUsage(), status="completed") is False
    def explode():
        raise RuntimeError("boom")
    assert record_usage_event(explode, kind="chat", provider="zen", model="m", usage=TokenUsage(output=1), status="completed") is False
    tracker = UsageTracker(explode, "chat")
    tracker.track("zen", "m", object())  # A provider without a usage attribute.
    tracker.finish("completed")


def test_usage_api_summary_overrides_and_refresh_floor(monkeypatch, tmp_path) -> None:
    book = book_with(tmp_path)
    monkeypatch.setattr(main_module, "price_book", book)
    record_usage_event(main_module.SessionLocal, kind="title", provider="zen", model="api-test-model", usage=TokenUsage(uncached_input=2_000_000, output=1_000_000), status="completed")
    summary = client.get("/api/usage/summary", params={"days": 7, "tz": "America/Chicago", "kind": "title"}).json()
    model = next(item for item in summary["models"] if item["model"] == "api-test-model")
    assert model["cost_unknown"] is True and model["rates"] is None
    saved = client.put("/api/usage/price-overrides", json={"model": "api-test-model", "input": 1.5, "output": 6})
    assert saved.status_code == 200 and saved.json()["api-test-model"]["cache_read"] is None
    summary = client.get("/api/usage/summary", params={"days": 7, "kind": "title", "provider": "zen"}).json()
    model = next(item for item in summary["models"] if item["model"] == "api-test-model")
    assert model["cost_usd"] == pytest.approx(9.0) and model["rate_source"] == "override"
    assert model["rates"] == {"input": 1.5, "output": 6.0, "cache_read": 1.5, "cache_write": 1.5}
    assert client.delete("/api/usage/price-overrides", params={"model": "api-test-model"}).json().get("api-test-model") is None
    assert client.get("/api/usage/summary", params={"days": 5}).status_code == 422
    assert client.put("/api/usage/price-overrides", json={"model": "x", "input": -1, "output": 1}).status_code == 422
    book.transport = counting_transport({})
    assert client.post("/api/usage/prices/refresh").status_code == 200
    again = client.post("/api/usage/prices/refresh")
    assert again.status_code == 429 and int(again.headers["retry-after"]) > 0

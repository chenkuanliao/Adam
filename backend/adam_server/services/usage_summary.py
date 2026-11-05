"""Aggregate stored usage events into the dashboard summary, pricing every event at read time."""
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .pricing import COST_SOURCES, PriceBook, Rates, price_event

RANGE_DAYS = (1, 7, 30, 90)
CATEGORIES = ("input", "cache_read", "cache_write", "output")
TOKEN_FIELDS = ("uncached_input", "cache_read", "cache_write", "output", "reasoning")


@dataclass(frozen=True)
class UsageRow:
    """The columns of a stored usage event that pricing and aggregation need."""
    created_at: datetime
    kind: str
    provider: str
    model_id: str
    uncached_input_tokens: int
    cache_read_tokens: int
    cache_write_tokens: int
    output_tokens: int
    reasoning_tokens: int
    provider_reported_cost_usd: float | None
    status: str


def resolve_zone(name: str | None) -> ZoneInfo | timezone:
    try:
        return ZoneInfo(name) if name else timezone.utc
    except (ZoneInfoNotFoundError, ValueError):
        return timezone.utc


def usage_window(days: int, now: datetime, zone) -> tuple[datetime, list[str], str]:
    """(UTC start, bucket labels, resolution). Past 24h is hourly; longer ranges use local days."""
    local_now = now.astimezone(zone)
    if days == 1:
        current_hour = local_now.replace(minute=0, second=0, microsecond=0)
        hours = [current_hour - timedelta(hours=offset) for offset in range(23, -1, -1)]
        return hours[0].astimezone(timezone.utc), [hour.isoformat() for hour in hours], "hour"
    today = local_now.date()
    labels = [(today - timedelta(days=offset)).isoformat() for offset in range(days - 1, -1, -1)]
    start = datetime.combine(today - timedelta(days=days - 1), datetime.min.time(), tzinfo=zone)
    return start.astimezone(timezone.utc), labels, "day"


def _bucket(created_at: datetime, zone, resolution: str) -> str:
    stamp = (created_at if created_at.tzinfo else created_at.replace(tzinfo=timezone.utc)).astimezone(zone)
    if resolution == "hour":
        return stamp.replace(minute=0, second=0, microsecond=0).isoformat()
    return stamp.date().isoformat()


def _empty_totals() -> dict[str, Any]:
    return {"requests": 0, "cost_usd": 0.0, "cache_savings_usd": 0.0, "total_tokens": 0, "unpriced_requests": 0, "unpriced_tokens": 0,
            "tokens": dict.fromkeys(TOKEN_FIELDS, 0), "category_cost": {**dict.fromkeys(CATEGORIES, 0.0), "other": 0.0},
            "cost_sources": dict.fromkeys(COST_SOURCES, 0)}


def _add(target: dict, row: UsageRow, priced, total_tokens: int) -> None:
    target["requests"] += 1
    target["cost_usd"] += priced.cost_usd
    target["cache_savings_usd"] += priced.cache_savings_usd
    target["total_tokens"] += total_tokens
    target["cost_sources"][priced.cost_source] += 1
    if priced.cost_source == "unpriced":
        target["unpriced_requests"] += 1
        target["unpriced_tokens"] += total_tokens
    tokens = target["tokens"]
    tokens["uncached_input"] += row.uncached_input_tokens
    tokens["cache_read"] += row.cache_read_tokens
    tokens["cache_write"] += row.cache_write_tokens
    tokens["output"] += row.output_tokens
    tokens["reasoning"] += row.reasoning_tokens
    if priced.category_cost is None:
        target["category_cost"]["other"] += priced.cost_usd
    else:
        for name in CATEGORIES:
            target["category_cost"][name] += priced.category_cost[name]


def _share(part: float, whole: float) -> float:
    return part / whole if whole > 0 else 0.0


def build_usage_summary(rows: list[UsageRow], *, book: PriceBook, overrides: dict[str, Rates], days: int, now: datetime,
                        zone, provider: str | None = None, kind: str | None = None) -> dict[str, Any]:
    """`rows` should already be limited to the window; filters are applied again defensively."""
    start, labels, resolution = usage_window(days, now, zone)
    in_window = [row for row in rows
                 if (row.created_at if row.created_at.tzinfo else row.created_at.replace(tzinfo=timezone.utc)) >= start
                 and (kind is None or row.kind == kind)]
    available_providers = sorted({row.provider for row in in_window})
    selected = [row for row in in_window if provider is None or row.provider == provider]

    rate_cache: dict[tuple[str, str], tuple[Rates | None, str | None]] = {}
    totals = _empty_totals()
    providers: dict[str, dict] = defaultdict(_empty_totals)
    models: dict[tuple[str, str], dict] = {}
    periods: dict[str, dict] = {label: {**_empty_totals(), "by_provider": defaultdict(lambda: {"cost_usd": 0.0, "total_tokens": 0, "requests": 0})} for label in labels}
    kinds: dict[str, dict] = defaultdict(_empty_totals)

    for row in selected:
        key = (row.provider, row.model_id)
        if key not in rate_cache:
            rate_cache[key] = book.resolve(row.provider, row.model_id, overrides)
        rates, rate_source = rate_cache[key]
        priced = price_event(rates, provider=row.provider, uncached_input=row.uncached_input_tokens, cache_read=row.cache_read_tokens,
                             cache_write=row.cache_write_tokens, output=row.output_tokens,
                             reported_cost_usd=row.provider_reported_cost_usd, override=rate_source == "override")
        total_tokens = row.uncached_input_tokens + row.cache_read_tokens + row.cache_write_tokens + row.output_tokens
        model = models.get(key)
        if model is None:
            model = models[key] = {**_empty_totals(), "provider": row.provider, "model": row.model_id,
                                   "rates": rates.as_per_million() if rates else None, "rate_source": rate_source,
                                   "series": {label: {"cost_usd": 0.0, "total_tokens": 0} for label in labels}}
        bucket = _bucket(row.created_at, zone, resolution)
        for target in (totals, providers[row.provider], model, kinds[row.kind]):
            _add(target, row, priced, total_tokens)
        if bucket in periods:
            period = periods[bucket]
            _add(period, row, priced, total_tokens)
            by_provider = period["by_provider"][row.provider]
            by_provider["cost_usd"] += priced.cost_usd
            by_provider["total_tokens"] += total_tokens
            by_provider["requests"] += 1
            model["series"][bucket]["cost_usd"] += priced.cost_usd
            model["series"][bucket]["total_tokens"] += total_tokens

    total_cost, total_tokens = totals["cost_usd"], totals["total_tokens"]
    provider_list = sorted(({**value, "provider": name, "cost_share": _share(value["cost_usd"], total_cost),
                             "token_share": _share(value["total_tokens"], total_tokens)} for name, value in providers.items()),
                           key=lambda item: (-item["cost_usd"], -item["total_tokens"]))
    model_list = []
    for model in models.values():
        model["cost_share"] = _share(model["cost_usd"], total_cost)
        model["token_share"] = _share(model["total_tokens"], total_tokens)
        model["cost_unknown"] = model["unpriced_requests"] == model["requests"]
        # Compact per-bucket arrays aligned with range.buckets keep long ranges small.
        points = model.pop("series").values()
        model["series_cost"] = [round(point["cost_usd"], 6) for point in points]
        model["series_tokens"] = [point["total_tokens"] for point in points]
        model_list.append(model)
    model_list.sort(key=lambda item: (-item["cost_usd"], -item["total_tokens"]))
    series = [{"bucket": label, "requests": period["requests"], "cost_usd": round(period["cost_usd"], 6), "total_tokens": period["total_tokens"],
               "by_provider": {name: {**value, "cost_usd": round(value["cost_usd"], 6)} for name, value in period["by_provider"].items()}}
              for label, period in periods.items()]
    return {"range": {"days": days, "resolution": resolution, "since": start.isoformat(), "until": now.isoformat(), "buckets": labels},
            "filters": {"provider": provider, "kind": kind}, "available_providers": available_providers,
            "totals": totals, "providers": provider_list, "models": model_list, "series": series,
            "kinds": {name: {key: value for key, value in data.items() if key in ("requests", "cost_usd", "total_tokens")} for name, data in kinds.items()},
            "pricing": book.status()}

#!/usr/bin/env python3
"""Seed synthetic usage events so the Usage dashboard can be viewed without spending money.

Development only; never run automatically. Rows use ids starting with ``demo-usage-`` so a rerun
replaces them and ``--clear`` removes them without touching real usage history.

    python scripts/seed-usage-demo.py                 # ./data/database/adam.sqlite3
    python scripts/seed-usage-demo.py --data-dir data/review-demo
    python scripts/seed-usage-demo.py --clear

Start the app once first so the database and its ``usage_events`` table exist.
"""
import argparse
import random
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

PREFIX = "demo-usage-"

# (provider, model, kind, weight, uncached input, cache read, cache write, output, reasoning, reports cost)
PROFILES = [
    ("zen", "gpt-5.6-terra", "chat", 14, (6_000, 40_000), (0, 30_000), (0, 0), (300, 1_800), 0.3, False),
    ("zen", "gpt-6-luna", "title", 10, (600, 3_000), (0, 0), (0, 0), (8, 20), 0.0, False),
    ("zen", "claude-sonnet-5-5", "chat", 5, (200, 1_500), (20_000, 60_000), (0, 25_000), (400, 2_000), 0.0, False),
    ("zen", "adam-lab-preview", "quick_ask", 2, (800, 3_000), (0, 0), (0, 0), (100, 600), 0.0, False),
    ("anthropic", "claude-sonnet-4-5", "chat", 7, (100, 1_200), (25_000, 70_000), (0, 30_000), (300, 1_500), 0.2, False),
    ("anthropic", "claude-haiku-4-5", "quick_ask", 4, (900, 3_500), (0, 0), (0, 0), (80, 500), 0.0, False),
    ("openai", "gpt-5.6-terra", "chat", 5, (5_000, 35_000), (0, 25_000), (0, 0), (500, 3_000), 0.6, False),
    ("openrouter", "anthropic/claude-sonnet-4.5", "chat", 3, (1_000, 20_000), (0, 40_000), (0, 10_000), (300, 1_400), 0.0, True),
    ("google", "gemini-2.5-pro", "chat", 3, (4_000, 30_000), (0, 30_000), (0, 0), (300, 1_600), 0.4, False),
]


def database_path(data_dir: Path) -> Path:
    path = data_dir / "database" / "adam.sqlite3"
    if not path.is_file():
        raise SystemExit(f"No database at {path}. Start Adam once so migrations create it.")
    return path


def seed(db: sqlite3.Connection, days: int, per_day: int, rng: random.Random) -> int:
    now = datetime.now(timezone.utc)
    weights = [profile[3] for profile in PROFILES]
    rows = []
    for day in range(days):
        # Busier recent weeks and weekdays, with a quiet gap or two.
        activity = (1.4 if day < 14 else 0.8) * (0.4 if (now - timedelta(days=day)).weekday() >= 5 else 1.0)
        if day in (18, 19, 41):
            continue
        for index in range(max(1, round(per_day * activity * rng.uniform(0.5, 1.4)))):
            provider, model, kind, _, uncached, read, write, output, reasoning_share, reports = rng.choices(PROFILES, weights)[0]
            created = now - timedelta(days=day, seconds=rng.randint(60, 86_000))
            cache_read = rng.randint(*read) if read[1] and rng.random() < 0.75 else 0
            cache_write = rng.randint(*write) if write[1] and not cache_read else 0
            out = rng.randint(*output)
            uncached_in = rng.randint(*uncached)
            cost = None
            if reports:  # OpenRouter-style reported cost, close to list price.
                cost = round((uncached_in * 3 + cache_read * 0.3 + cache_write * 3.75 + out * 15) / 1e6 * rng.uniform(0.95, 1.05), 6)
            rows.append((f"{PREFIX}{day:03d}-{index:03d}", created.strftime("%Y-%m-%d %H:%M:%S.%f"), kind, provider, model,
                         uncached_in, cache_read, cache_write, out, int(out * reasoning_share), cost,
                         "partial" if rng.random() < 0.03 else "completed"))
    db.executemany("""INSERT INTO usage_events (id, created_at, kind, provider, model_id, uncached_input_tokens, cache_read_tokens,
                      cache_write_tokens, output_tokens, reasoning_tokens, provider_reported_cost_usd, status)
                      VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""", rows)
    return len(rows)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--data-dir", type=Path, default=Path("data"), help="Adam data directory (default: ./data)")
    parser.add_argument("--days", type=int, default=90)
    parser.add_argument("--per-day", type=int, default=9, help="Average requests per day")
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--clear", action="store_true", help="Only remove previously seeded demo rows")
    args = parser.parse_args()
    with sqlite3.connect(database_path(args.data_dir), timeout=10) as db:
        removed = db.execute("DELETE FROM usage_events WHERE id LIKE ?", (f"{PREFIX}%",)).rowcount
        added = 0 if args.clear else seed(db, args.days, args.per_day, random.Random(args.seed))
    print(f"Removed {removed} demo usage events; added {added}.")


if __name__ == "__main__":
    main()

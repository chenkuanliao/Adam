/** Pure helpers for the usage dashboard, kept separate so they can be unit-tested. */

export function formatUsd(value: number): string {
  if (value > 0 && value < 0.01) return '<$0.01';
  return new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD', minimumFractionDigits: value >= 1000 ? 0 : 2, maximumFractionDigits: value >= 1000 ? 0 : 2 }).format(value);
}

export function formatTokens(value: number): string {
  if (value >= 1e9) return `${(value / 1e9).toFixed(value >= 1e10 ? 0 : 1)}B`;
  if (value >= 1e6) return `${(value / 1e6).toFixed(value >= 1e7 ? 0 : 1)}M`;
  if (value >= 1e3) return `${(value / 1e3).toFixed(value >= 1e4 ? 0 : 1)}K`;
  return String(Math.round(value));
}

export const formatPercent = (value: number) => `${value > 0 && value < 0.01 ? '<1' : Math.round(value * 100)}%`;

/** A rounded axis maximum at or above the value whose quarters are also round (4 gridlines). */
export function niceMax(value: number): number {
  if (value <= 0) return 1;
  const magnitude = 10 ** Math.floor(Math.log10(value));
  return ([1, 2, 3, 4, 6, 8, 10].find((step) => step * magnitude >= value) ?? 10) * magnitude;
}

/** Stack series in the given order: each layer spans from the previous layer's top. */
export function stackSeries<T extends { values: number[] }>(series: T[], length: number): (T & { lower: number[]; upper: number[] })[] {
  return series.reduce<(T & { lower: number[]; upper: number[] })[]>((layers, item) => {
    const lower = layers.length ? layers[layers.length - 1].upper : Array.from({ length }, () => 0);
    return [...layers, { ...item, lower, upper: lower.map((value, index) => value + (item.values[index] ?? 0)) }];
  }, []);
}

/** Cache reads as a share of all input; cache writes count as misses. */
export function cacheHitRate(tokens: { uncached_input: number; cache_read: number; cache_write: number }): number | null {
  const input = tokens.uncached_input + tokens.cache_read + tokens.cache_write;
  return input === 0 ? null : tokens.cache_read / input;
}

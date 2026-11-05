'use client';

import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState, type FormEvent, type ReactNode } from 'react';
import AppSelect from './app-select';
import { cacheHitRate, formatPercent, formatTokens, formatUsd, niceMax, stackSeries } from './usage-format';

type TokenTotals = { uncached_input: number; cache_read: number; cache_write: number; output: number; reasoning: number };
type CategoryCost = { input: number; cache_read: number; cache_write: number; output: number; other: number };
type CostSource = 'providerReported' | 'modelPriced' | 'apiEstimate' | 'unpriced';
type Totals = {
  requests: number; cost_usd: number; cache_savings_usd: number; total_tokens: number; unpriced_requests: number; unpriced_tokens: number;
  tokens: TokenTotals; category_cost: CategoryCost; cost_sources: Record<CostSource, number>;
};
type PerMillion = { input: number; output: number; cache_read: number; cache_write: number };
type ModelTotals = Totals & {
  provider: string; model: string; cost_share: number; token_share: number; cost_unknown: boolean;
  rates: PerMillion | null; rate_source: 'override' | 'models.dev' | 'litellm' | null;
  series: { bucket: string; cost_usd: number; total_tokens: number }[];
};
type ProviderTotals = Totals & { provider: string; cost_share: number; token_share: number };
type Period = Totals & { bucket: string; by_provider: Record<string, { cost_usd: number; total_tokens: number; requests: number }> };
type PricingStatus = { litellm_fetched_at: string | null; models_dev_fetched_at: string | null; litellm_models: number; zen_models: number; stale: boolean; error: string | null; refresh_available_in: number };
export type UsageSummary = {
  range: { days: number; resolution: 'day' | 'hour'; since: string; until: string; buckets: string[] };
  available_providers: string[]; totals: Totals; providers: ProviderTotals[]; models: ModelTotals[]; series: Period[]; pricing: PricingStatus;
};
type Metric = 'cost' | 'tokens';
type PriceOverride = { input: number; output: number; cache_read: number | null; cache_write: number | null };

export const CHATGPT_USAGE_URL = 'https://chatgpt.com/#settings/Usage';
const RANGES = [{ days: 1, label: 'Past 24h' }, { days: 7, label: '7 days' }, { days: 30, label: '30 days' }, { days: 90, label: '90 days' }] as const;
const KINDS = [{ value: '', label: 'All requests' }, { value: 'chat', label: 'Chat' }, { value: 'quick_ask', label: 'Quick Ask' }, { value: 'title', label: 'Chat titles' }];
/** Fixed categorical slots: a provider keeps its color whatever the filter shows. */
const PROVIDERS: Record<string, { label: string; slot: number; mark: string; manageUrl?: string }> = {
  zen: { label: 'OpenCode Zen', slot: 1, mark: 'Z' },
  openrouter: { label: 'OpenRouter', slot: 2, mark: 'OR' },
  openai: { label: 'OpenAI', slot: 3, mark: 'O' },
  anthropic: { label: 'Anthropic', slot: 4, mark: 'A' },
  google: { label: 'Google', slot: 5, mark: 'G' },
  chatgpt: { label: 'ChatGPT (subscription)', slot: 6, mark: 'C', manageUrl: CHATGPT_USAGE_URL },
};
const PROVIDER_ORDER = Object.keys(PROVIDERS);
const PREFERENCES_KEY = 'adam.usage.preferences';

export function providerInfo(provider: string) {
  return PROVIDERS[provider] ?? { label: provider, slot: 7, mark: provider.slice(0, 1).toUpperCase() };
}
const providerColor = (provider: string) => `var(--usage-series-${providerInfo(provider).slot})`;
const sortProviders = (providers: string[]) => [...providers].sort((a, b) => (PROVIDER_ORDER.indexOf(a) + 1 || 99) - (PROVIDER_ORDER.indexOf(b) + 1 || 99) || a.localeCompare(b));

const formatRate = (value: number) => `$${value >= 1 ? value.toFixed(2) : value.toPrecision(2)}`;

function formatBucket(bucket: string, resolution: 'day' | 'hour', long = false): string {
  if (resolution === 'hour') return new Date(bucket).toLocaleTimeString('en-US', { hour: 'numeric', ...(long ? { minute: '2-digit', month: 'short', day: 'numeric' } : {}) });
  const [year, month, day] = bucket.split('-').map(Number);
  return new Date(year, month - 1, day).toLocaleDateString('en-US', { month: 'short', day: 'numeric', ...(long ? { weekday: 'short' } : {}) });
}

function readPreferences(): { days: number; metric: Metric } {
  try {
    const value = JSON.parse(window.localStorage.getItem(PREFERENCES_KEY) ?? '{}');
    return { days: RANGES.some((range) => range.days === value.days) ? value.days : 7, metric: value.metric === 'tokens' ? 'tokens' : 'cost' };
  } catch {
    return { days: 7, metric: 'cost' };
  }
}

export function ProviderMark({ provider }: { provider: string }) {
  const info = providerInfo(provider);
  return <span className="usage-provider-mark" style={{ '--mark-color': providerColor(provider) } as React.CSSProperties} aria-hidden="true">{info.mark}</span>;
}

/* ------------------------------------------------------------------------------------------ */
/* Stacked area chart: one series per provider, fixed order, crosshair tooltip                */
/* ------------------------------------------------------------------------------------------ */

type ChartSeries = { key: string; label: string; color: string; values: number[] };

export function UsageAreaChart({ buckets, resolution, series, format, label, height = 220 }: {
  buckets: string[]; resolution: 'day' | 'hour'; series: ChartSeries[]; format: (value: number) => string; label: string; height?: number;
}) {
  const host = useRef<HTMLDivElement>(null);
  const [width, setWidth] = useState(640);
  const [hover, setHover] = useState<number | null>(null);
  useLayoutEffect(() => {
    const element = host.current;
    if (!element) return;
    const measure = () => setWidth(Math.max(240, element.clientWidth));
    measure();
    const observer = new ResizeObserver(measure);
    observer.observe(element);
    return () => observer.disconnect();
  }, []);
  const padding = { top: 10, right: 8, bottom: 26, left: 56 };
  const plotWidth = width - padding.left - padding.right;
  const plotHeight = height - padding.top - padding.bottom;
  const stacks = useMemo(() => stackSeries(series, buckets.length), [buckets, series]);
  const totals = stacks.length ? stacks[stacks.length - 1].upper : buckets.map(() => 0);
  const max = niceMax(Math.max(0, ...totals));
  const x = (index: number) => padding.left + (buckets.length <= 1 ? plotWidth / 2 : (index / (buckets.length - 1)) * plotWidth);
  const y = (value: number) => padding.top + plotHeight - (value / max) * plotHeight;
  const ticks = [0, 0.25, 0.5, 0.75, 1].map((step) => step * max);
  const labelEvery = Math.max(1, Math.ceil(buckets.length / Math.max(2, Math.floor(plotWidth / 76))));
  const empty = totals.every((value) => value === 0);

  function pointerMove(event: React.PointerEvent<SVGRectElement>) {
    const bounds = event.currentTarget.getBoundingClientRect();
    const ratio = (event.clientX - bounds.left) / Math.max(1, bounds.width);
    setHover(Math.min(buckets.length - 1, Math.max(0, Math.round(ratio * (buckets.length - 1)))));
  }

  const tooltipLeft = hover === null ? 0 : x(hover);
  return <div className="usage-chart" ref={host}>
    <svg width={width} height={height} role="img" aria-label={`${label}: ${series.map((item) => `${item.label} ${format(item.values.reduce((sum, value) => sum + value, 0))}`).join(', ') || 'no usage'}`}>
      {ticks.map((tick) => <g key={tick} className="usage-chart-grid"><line x1={padding.left} x2={width - padding.right} y1={y(tick)} y2={y(tick)} /><text x={padding.left - 8} y={y(tick)} dy="0.32em" textAnchor="end">{format(tick)}</text></g>)}
      {buckets.map((bucket, index) => (index % labelEvery === 0 && (buckets.length - 1 - index >= labelEvery * 0.75 || index === 0)) || index === buckets.length - 1 ? <text key={bucket} className="usage-chart-axis" x={x(index)} y={height - 6} textAnchor={index === 0 ? 'start' : index === buckets.length - 1 ? 'end' : 'middle'}>{formatBucket(bucket, resolution)}</text> : null)}
      {!empty && stacks.map((item) => {
        const top = item.upper.map((value, index) => `${x(index)},${y(value)}`);
        const bottom = item.lower.map((value, index) => `${x(index)},${y(value)}`).reverse();
        return <g key={item.key}>
          <polygon points={[...top, ...bottom].join(' ')} fill={item.color} className="usage-chart-area" />
          <polyline points={top.join(' ')} fill="none" stroke={item.color} strokeWidth={2} strokeLinejoin="round" strokeLinecap="round" />
        </g>;
      })}
      {hover !== null && !empty && <g className="usage-chart-cursor">
        <line x1={x(hover)} x2={x(hover)} y1={padding.top} y2={padding.top + plotHeight} />
        {stacks.map((item) => (item.values[hover] ?? 0) > 0 ? <circle key={item.key} cx={x(hover)} cy={y(item.upper[hover])} r={4} fill={item.color} /> : null)}
      </g>}
      <rect x={padding.left} y={padding.top} width={plotWidth} height={plotHeight} fill="transparent" onPointerMove={pointerMove} onPointerLeave={() => setHover(null)} />
    </svg>
    {empty && <p className="usage-chart-empty">No usage in this window</p>}
    {hover !== null && !empty && <div className="usage-tooltip" style={{ left: Math.min(Math.max(tooltipLeft, 90), width - 90) }}>
      <strong>{formatBucket(buckets[hover], resolution, true)}</strong>
      {[...stacks].reverse().map((item) => <span key={item.key}><i style={{ background: item.color }} />{item.label}<b>{format(item.values[hover] ?? 0)}</b></span>)}
      {stacks.length > 1 && <span className="usage-tooltip-total">Total<b>{format(totals[hover])}</b></span>}
    </div>}
    {series.length > 1 && <div className="usage-legend">{series.map((item) => <span key={item.key}><i style={{ background: item.color }} />{item.label}</span>)}</div>}
  </div>;
}

/* ------------------------------------------------------------------------------------------ */
/* Share bar                                                                                   */
/* ------------------------------------------------------------------------------------------ */

type Segment = { label: string; value: number; color: string };
const TYPE_COLORS = { input: 'var(--usage-type-input)', cache_read: 'var(--usage-type-cache-read)', cache_write: 'var(--usage-type-cache-write)', output: 'var(--usage-type-output)', other: 'var(--usage-type-other)' };

export function costSegments(cost: CategoryCost): Segment[] {
  return [
    { label: 'Input', value: cost.input, color: TYPE_COLORS.input },
    { label: 'Cache read', value: cost.cache_read, color: TYPE_COLORS.cache_read },
    { label: 'Cache write', value: cost.cache_write, color: TYPE_COLORS.cache_write },
    { label: 'Output', value: cost.output, color: TYPE_COLORS.output },
    { label: 'Provider total', value: cost.other, color: TYPE_COLORS.other },
  ];
}
export function tokenSegments(tokens: TokenTotals): Segment[] {
  return [
    { label: 'Input', value: tokens.uncached_input, color: TYPE_COLORS.input },
    { label: 'Cache read', value: tokens.cache_read, color: TYPE_COLORS.cache_read },
    { label: 'Cache write', value: tokens.cache_write, color: TYPE_COLORS.cache_write },
    { label: 'Output', value: tokens.output, color: TYPE_COLORS.output },
  ];
}

function ShareBar({ label, segments, format, aside }: { label: string; segments: Segment[]; format: (value: number) => string; aside?: ReactNode }) {
  const visible = segments.filter((segment) => segment.value > 0);
  const total = visible.reduce((sum, segment) => sum + segment.value, 0);
  return <div className="usage-share">
    <div className="usage-share-head"><h3>{label}</h3>{aside}</div>
    {total > 0 ? <>
      <div className="usage-share-bar" role="img" aria-label={`${label}: ${visible.map((segment) => `${segment.label} ${format(segment.value)}`).join(', ')}`}>
        {visible.map((segment) => <span key={segment.label} title={`${segment.label} · ${format(segment.value)} · ${formatPercent(segment.value / total)}`} style={{ flexGrow: segment.value, background: segment.color }} />)}
      </div>
      <div className="usage-share-legend">{visible.map((segment) => <span key={segment.label}><i style={{ background: segment.color }} />{segment.label}<b>{format(segment.value)}</b><small>{formatPercent(segment.value / total)}</small></span>)}</div>
    </> : <p className="usage-muted">Nothing to show yet.</p>}
  </div>;
}

/* ------------------------------------------------------------------------------------------ */
/* Model detail and price override panels                                                     */
/* ------------------------------------------------------------------------------------------ */

function costPerMillion(model: ModelTotals): number | null {
  const priced = model.total_tokens - model.unpriced_tokens;
  return model.cost_unknown || priced <= 0 ? null : (model.cost_usd / priced) * 1e6;
}
const RATE_SOURCE_LABELS: Record<string, string> = { override: 'Your price override', 'models.dev': 'OpenCode Zen price list (models.dev)', litellm: 'LiteLLM price list' };

function ModelPanel({ model, summary, metric, onClose, onSetPrice }: { model: ModelTotals; summary: UsageSummary; metric: Metric; onClose: () => void; onSetPrice: () => void }) {
  const hit = cacheHitRate(model.tokens);
  const perMillion = costPerMillion(model);
  const chartMetric = model.cost_unknown ? 'tokens' : metric;
  const stats = [
    { label: 'Cost', value: model.cost_unknown ? 'Unpriced' : formatUsd(model.cost_usd) },
    { label: 'Tokens', value: formatTokens(model.total_tokens) },
    { label: 'Per 1M tokens', value: perMillion === null ? '—' : formatUsd(perMillion) },
    { label: 'Cache hit', value: hit === null ? '—' : formatPercent(hit) },
  ];
  return <div className="usage-sheet-backdrop" onMouseDown={(event) => { if (event.target === event.currentTarget) onClose(); }}>
    <section className="usage-sheet" role="dialog" aria-modal="true" aria-labelledby="usage-model-title">
      <header className="usage-sheet-head">
        <div><h2 id="usage-model-title"><ProviderMark provider={model.provider} />{model.model}</h2>
          <p>{providerInfo(model.provider).label} · {model.requests} {model.requests === 1 ? 'request' : 'requests'}{model.cost_unknown ? '' : ` · ${formatPercent(model.cost_share)} of cost`}</p></div>
        <button type="button" className="usage-icon-button" onClick={onClose} aria-label="Close model details">×</button>
      </header>
      <div className="usage-stat-grid">{stats.map((stat) => <div key={stat.label}><span>{stat.label}</span><strong>{stat.value}</strong></div>)}</div>
      <div className="usage-sheet-section">
        <h3>{summary.range.resolution === 'hour' ? 'Hourly' : 'Daily'} {chartMetric === 'cost' ? 'cost' : 'tokens'}</h3>
        <UsageAreaChart label={`${model.model} ${chartMetric}`} buckets={summary.range.buckets} resolution={summary.range.resolution} height={180}
          format={chartMetric === 'cost' ? formatUsd : formatTokens}
          series={[{ key: model.model, label: model.model, color: providerColor(model.provider), values: model.series.map((point) => chartMetric === 'cost' ? point.cost_usd : point.total_tokens) }]} />
      </div>
      <div className="usage-share-grid">
        {!model.cost_unknown && <ShareBar label="Cost by type" segments={costSegments(model.category_cost)} format={formatUsd} />}
        <ShareBar label="Tokens by type" segments={tokenSegments(model.tokens)} format={formatTokens} aside={model.tokens.reasoning > 0 ? <small className="usage-muted">{formatTokens(model.tokens.reasoning)} reasoning (in output)</small> : undefined} />
      </div>
      <footer className="usage-sheet-foot">
        <span className="usage-muted">{model.rates ? <>{RATE_SOURCE_LABELS[model.rate_source ?? ''] ?? 'Price'}: {formatRate(model.rates.input)} in · {formatRate(model.rates.output)} out · {formatRate(model.rates.cache_read)} cache read · {formatRate(model.rates.cache_write)} cache write per 1M</> : model.cost_sources.providerReported ? 'Cost reported by the provider; no list price found.' : `${formatTokens(model.unpriced_tokens)} tokens have no known price.`}</span>
        <button type="button" className={model.cost_unknown ? 'primary-button' : 'secondary-button'} onClick={onSetPrice}>{model.rate_source === 'override' ? 'Edit price' : 'Set price'}</button>
      </footer>
    </section>
  </div>;
}

function PriceForm({ model, current, override, onClose, onSaved }: { model: string | null; current: PerMillion | null; override: PriceOverride | undefined; onClose: () => void; onSaved: () => void }) {
  const initial = override ?? current;
  const field = (value: number | null | undefined) => value === null || value === undefined ? '' : String(Number(value.toPrecision(6)));
  const [name, setName] = useState(model ?? '');
  const [input, setInput] = useState(field(initial?.input));
  const [output, setOutput] = useState(field(initial?.output));
  const [cacheRead, setCacheRead] = useState(override ? field(override.cache_read) : '');
  const [cacheWrite, setCacheWrite] = useState(override ? field(override.cache_write) : '');
  const [status, setStatus] = useState('');
  const [saving, setSaving] = useState(false);
  const parse = (value: string) => value.trim() === '' ? null : Number(value);
  const values = [parse(input), parse(output), parse(cacheRead), parse(cacheWrite)];
  const valid = name.trim() && values[0] !== null && values[1] !== null && values.every((value) => value === null || (Number.isFinite(value) && value >= 0));

  async function save(event: FormEvent) {
    event.preventDefault();
    if (!valid) return;
    setSaving(true); setStatus('');
    try {
      const response = await fetch('/api/usage/price-overrides', { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ model: name.trim(), input: values[0], output: values[1], cache_read: values[2], cache_write: values[3] }) });
      if (!response.ok) throw new Error((await response.json().catch(() => ({}))).detail ?? 'Could not save the price.');
      onSaved();
    } catch (reason) {
      setStatus(reason instanceof Error ? reason.message : 'Could not save the price.');
    } finally { setSaving(false); }
  }
  async function remove() {
    setSaving(true);
    try {
      const response = await fetch(`/api/usage/price-overrides?model=${encodeURIComponent(name.trim())}`, { method: 'DELETE' });
      if (!response.ok) throw new Error('Could not remove the override.');
      onSaved();
    } catch (reason) {
      setStatus(reason instanceof Error ? reason.message : 'Could not remove the override.');
    } finally { setSaving(false); }
  }
  const numberField = (label: string, value: string, change: (value: string) => void, placeholder: string, required = false) =>
    <label className="field-label">{label}<span className="usage-money-input"><i>$</i><input type="number" inputMode="decimal" min="0" step="any" value={value} placeholder={placeholder} required={required} onChange={(event) => change(event.target.value)} /></span></label>;
  return <div className="usage-sheet-backdrop" onMouseDown={(event) => { if (event.target === event.currentTarget) onClose(); }}>
    <form className="usage-sheet usage-price-form" role="dialog" aria-modal="true" aria-labelledby="usage-price-title" onSubmit={save}>
      <header className="usage-sheet-head">
        <div><h2 id="usage-price-title">{override ? 'Edit price override' : 'Set model price'}</h2><p>USD per 1M tokens. Overrides win over published prices and apply to all past usage of this model.</p></div>
        <button type="button" className="usage-icon-button" onClick={onClose} aria-label="Close price form">×</button>
      </header>
      <label className="field-label">Model ID<input value={name} onChange={(event) => setName(event.target.value)} disabled={model !== null} placeholder="e.g. gpt-5.6-terra" required autoFocus={model === null} /></label>
      {!override && current && <p className="usage-muted">Prefilled with the current published price.</p>}
      <div className="usage-price-grid">
        {numberField('Input', input, setInput, '0.00', true)}
        {numberField('Output', output, setOutput, '0.00', true)}
        {numberField('Cache read', cacheRead, setCacheRead, input ? `${input} (input)` : 'Input rate')}
        {numberField('Cache write', cacheWrite, setCacheWrite, input ? `${input} (input)` : 'Input rate')}
      </div>
      <footer className="usage-sheet-foot">
        <span role="status" className="usage-error">{status}</span>
        <div>{override && <button type="button" className="secondary-button" onClick={() => void remove()} disabled={saving}>Remove override</button>}
          <button type="button" className="secondary-button" onClick={onClose}>Cancel</button>
          <button type="submit" className="primary-button" disabled={!valid || saving}>{saving ? 'Saving…' : 'Save price'}</button></div>
      </footer>
    </form>
  </div>;
}

/* ------------------------------------------------------------------------------------------ */
/* Page                                                                                         */
/* ------------------------------------------------------------------------------------------ */

function Metric({ label, value, hint }: { label: string; value: string; hint?: string }) {
  return <div className="usage-metric" title={hint}><span>{label}</span><strong>{value}</strong></div>;
}

export default function UsageDashboard({ onClose, onBack }: { onClose: () => void; onBack?: () => void }) {
  const dialog = useRef<HTMLDialogElement>(null);
  const [preferences, setPreferences] = useState<{ days: number; metric: Metric }>(readPreferences);
  const [provider, setProvider] = useState('');
  const [kind, setKind] = useState('');
  const [breakdown, setBreakdown] = useState<'model' | 'day'>('model');
  const [summary, setSummary] = useState<UsageSummary | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [refreshing, setRefreshing] = useState(false);
  const [selectedModel, setSelectedModel] = useState<string | null>(null);
  const [priceTarget, setPriceTarget] = useState<{ model: string | null } | null>(null);
  const [overrides, setOverrides] = useState<Record<string, PriceOverride>>({});
  const request = useRef(0);
  const { days, metric } = preferences;

  const updatePreferences = (next: Partial<{ days: number; metric: Metric }>) => setPreferences((current) => {
    const value = { ...current, ...next };
    try { window.localStorage.setItem(PREFERENCES_KEY, JSON.stringify(value)); } catch { /* Preferences are a convenience. */ }
    return value;
  });

  const load = useCallback(async () => {
    const id = ++request.current;
    setLoading(true);
    try {
      const timeZone = Intl.DateTimeFormat().resolvedOptions().timeZone;
      const params = new URLSearchParams({ days: String(days), tz: timeZone });
      if (provider) params.set('provider', provider);
      if (kind) params.set('kind', kind);
      const [response, overrideResponse] = await Promise.all([fetch(`/api/usage/summary?${params}`), fetch('/api/usage/price-overrides')]);
      if (!response.ok) throw new Error('Could not load usage.');
      const value = await response.json() as UsageSummary;
      const overrideValue = overrideResponse.ok ? await overrideResponse.json() as Record<string, PriceOverride> : {};
      if (id !== request.current) return;
      setSummary(value); setOverrides(overrideValue); setError('');
    } catch (reason) {
      if (id === request.current) setError(reason instanceof Error ? reason.message : 'Could not load usage.');
    } finally {
      if (id === request.current) setLoading(false);
    }
  }, [days, provider, kind]);
  // eslint-disable-next-line react-hooks/set-state-in-effect -- reload when the range or filters change
  useEffect(() => { void load(); }, [load]);

  useEffect(() => {
    const element = dialog.current;
    if (element && !element.open) element.showModal();
  }, []);
  // Escape closes the innermost panel first.
  const onCancel = (event: React.SyntheticEvent) => {
    event.preventDefault();
    if (priceTarget) setPriceTarget(null);
    else if (selectedModel) setSelectedModel(null);
    else onClose();
  };

  async function refreshPrices() {
    setRefreshing(true); setNotice('');
    try {
      const response = await fetch('/api/usage/prices/refresh', { method: 'POST' });
      const value = await response.json().catch(() => ({}));
      if (response.status === 429) setNotice(value.detail ?? 'Prices were refreshed recently.');
      else if (!response.ok) throw new Error('Could not refresh prices.');
      else setNotice(value.error ? `Some price sources failed (${value.error}); using the last saved prices.` : 'Prices refreshed.');
      await load();
    } catch (reason) {
      setNotice(reason instanceof Error ? reason.message : 'Could not refresh prices.');
    } finally { setRefreshing(false); }
  }

  const totals = summary?.totals;
  const activeProviders = useMemo(() => sortProviders(summary?.providers.map((item) => item.provider) ?? []), [summary]);
  const chartSeries = useMemo<ChartSeries[]>(() => !summary ? [] : activeProviders.map((name) => ({
    key: name, label: providerInfo(name).label, color: providerColor(name),
    values: summary.series.map((period) => metric === 'cost' ? period.by_provider[name]?.cost_usd ?? 0 : period.by_provider[name]?.total_tokens ?? 0),
  })), [summary, activeProviders, metric]);
  const models = useMemo(() => [...(summary?.models ?? [])].sort((a, b) => metric === 'tokens' ? b.total_tokens - a.total_tokens : (Number(a.cost_unknown) - Number(b.cost_unknown)) || b.cost_usd - a.cost_usd || b.total_tokens - a.total_tokens), [summary, metric]);
  const peak = Math.max(0, ...models.map((model) => metric === 'tokens' ? model.total_tokens : model.cost_usd));
  const periods = useMemo(() => [...(summary?.series ?? [])].filter((period) => period.requests > 0).reverse(), [summary]);
  const selected = summary?.models.find((model) => `${model.provider}:${model.model}` === selectedModel);
  const priceModel = priceTarget?.model ? summary?.models.find((model) => model.model === priceTarget.model) : undefined;
  const unpricedShare = totals && totals.requests ? totals.unpriced_requests / totals.requests : 0;
  const estimateLabel = totals && totals.requests > 0 && totals.cost_sources.providerReported === totals.requests ? 'provider reported' : 'estimate';
  const pricing = summary?.pricing;
  const pricingDate = pricing?.litellm_fetched_at ? new Date(pricing.litellm_fetched_at).toLocaleString('en-US', { month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' }) : null;
  const providerOptions = [{ value: '', label: 'All providers' }, ...sortProviders([...new Set([...(summary?.available_providers ?? []), ...(provider ? [provider] : [])])]).map((name) => ({ value: name, label: providerInfo(name).label }))];

  return <dialog ref={dialog} className="usage-dialog" aria-labelledby="usage-title" onCancel={onCancel}>
    <div className="usage-page">
      <header className="usage-topbar">
        <div className="usage-title">
          {onBack && <button type="button" className="usage-back" onClick={onBack} aria-label="Back to settings">‹ Settings</button>}
          <div><span className="settings-kicker">Preferences</span><h1 id="usage-title">Usage</h1></div>
        </div>
        <button type="button" className="usage-icon-button" onClick={onClose} aria-label="Close usage">×</button>
      </header>

      <div className="usage-controls" role="toolbar" aria-label="Usage filters">
        <div className="usage-segmented" role="group" aria-label="Time range">
          {RANGES.map((range) => <button type="button" key={range.days} aria-pressed={days === range.days} onClick={() => updatePreferences({ days: range.days })}>{range.label}</button>)}
        </div>
        <div className="usage-segmented" role="group" aria-label="Metric">
          {(['cost', 'tokens'] as const).map((value) => <button type="button" key={value} aria-pressed={metric === value} onClick={() => updatePreferences({ metric: value })}>{value === 'cost' ? 'Cost' : 'Tokens'}</button>)}
        </div>
        <div className="usage-select"><AppSelect label="Provider" value={provider} onChange={setProvider} options={providerOptions} /></div>
        <div className="usage-select"><AppSelect label="Request type" value={kind} onChange={setKind} options={KINDS} /></div>
        <button type="button" className="secondary-button usage-refresh" onClick={() => void refreshPrices()} disabled={refreshing}
          title={pricing ? `LiteLLM prices: ${pricing.litellm_models} models${pricingDate ? `, fetched ${pricingDate}` : ''}. OpenCode Zen prices: ${pricing.zen_models} models.` : undefined}>
          <span aria-hidden="true" className={refreshing ? 'usage-spin' : ''}>↻</span>{refreshing ? 'Refreshing…' : 'Refresh prices'}
        </button>
      </div>
      {(notice || error || pricing?.error) && <p className={`usage-notice${error ? ' usage-error' : ''}`} role="status">{error || notice || `Price update failed; using prices from ${pricingDate ?? 'the last saved copy'}.`}</p>}

      {!summary ? <div className="usage-loading">{loading ? 'Loading usage…' : 'Usage is unavailable.'}</div> : <div className={`usage-body${loading ? ' is-loading' : ''}`}>
        <section className="usage-hero">
          <div className="usage-hero-summary">
            <span className="usage-hero-figure">{metric === 'cost' ? formatUsd(totals!.cost_usd) : formatTokens(totals!.total_tokens)}</span>
            <span className="usage-muted">{totals!.requests.toLocaleString()} {totals!.requests === 1 ? 'request' : 'requests'}{metric === 'cost' ? ` · ${estimateLabel}` : ' · processed tokens'}
              {metric === 'cost' && unpricedShare > 0 && <span className="usage-flag" title="Unpriced usage counts as $0. Set a price for those models below.">{formatPercent(unpricedShare)} unpriced</span>}</span>
            <ul className="usage-provider-list">
              {summary.providers.length === 0 && <li className="usage-muted">No requests yet in this window.</li>}
              {[...summary.providers].sort((a, b) => metric === 'tokens' ? b.total_tokens - a.total_tokens : b.cost_usd - a.cost_usd).map((item) => {
                const info = providerInfo(item.provider);
                return <li key={item.provider}>
                  <div className="usage-provider-row"><span className="usage-provider-name"><i style={{ background: providerColor(item.provider) }} /><ProviderMark provider={item.provider} />{info.label}<small>{item.requests} {item.requests === 1 ? 'request' : 'requests'}</small></span>
                    <strong>{metric === 'cost' ? formatUsd(item.cost_usd) : formatTokens(item.total_tokens)}</strong></div>
                  <span className="usage-muted usage-provider-sub">{metric === 'cost' ? `${formatPercent(item.cost_share)} of cost · ${formatTokens(item.total_tokens)} tokens` : `${formatPercent(item.token_share)} of tokens · ${formatUsd(item.cost_usd)}`}
                    {info.manageUrl && <a href={info.manageUrl} target="_blank" rel="noreferrer">Manage usage ↗</a>}</span>
                </li>;
              })}
            </ul>
          </div>
          <div className="usage-hero-chart">
            <h2>{summary.range.resolution === 'hour' ? 'Hourly' : 'Daily'} {metric === 'cost' ? 'cost' : 'processed tokens'}</h2>
            <UsageAreaChart label={`${summary.range.resolution === 'hour' ? 'Hourly' : 'Daily'} ${metric}`} buckets={summary.range.buckets} resolution={summary.range.resolution} series={chartSeries} format={metric === 'cost' ? formatUsd : formatTokens} />
          </div>
        </section>

        <section className="usage-section">
          <h2>Totals</h2>
          <div className="usage-metrics">
            <Metric label="Processed tokens" value={formatTokens(totals!.total_tokens)} hint="Uncached input + cache reads + cache writes + output" />
            <Metric label="Cached input" value={formatTokens(totals!.tokens.cache_read)} />
            <Metric label="Uncached input" value={formatTokens(totals!.tokens.uncached_input + totals!.tokens.cache_write)} hint="Includes cache writes" />
            <Metric label="Output" value={formatTokens(totals!.tokens.output)} hint={`Includes ${formatTokens(totals!.tokens.reasoning)} reasoning tokens`} />
            <Metric label="Cache savings" value={formatUsd(totals!.cache_savings_usd)} hint="Cache reads × (input price − cache read price)" />
          </div>
        </section>

        {totals!.total_tokens > 0 && <section className="usage-share-grid usage-section">
          {metric === 'cost' ? <ShareBar label="Cost by type" segments={costSegments(totals!.category_cost)} format={formatUsd} /> : <ShareBar label="Tokens by type" segments={tokenSegments(totals!.tokens)} format={formatTokens} />}
          {metric === 'cost' && <ShareBar label="Tokens by type" segments={tokenSegments(totals!.tokens)} format={formatTokens} />}
        </section>}

        <section className="usage-section">
          <div className="usage-section-head"><h2>Breakdown</h2>
            <div className="usage-segmented" role="group" aria-label="Breakdown">
              <button type="button" aria-pressed={breakdown === 'model'} onClick={() => setBreakdown('model')}>Model</button>
              <button type="button" aria-pressed={breakdown === 'day'} onClick={() => setBreakdown('day')}>{summary.range.resolution === 'hour' ? 'Hour' : 'Day'}</button>
            </div></div>
          {breakdown === 'model' ? <table className="usage-table">
            <thead><tr><th>#</th><th className="usage-col-model">Model</th><th>Cost</th><th className="usage-col-share">Share</th><th>Tokens</th></tr></thead>
            <tbody>
              {models.length === 0 ? <tr><td colSpan={5} className="usage-table-empty">No activity in this window.</td></tr> : models.map((model, index) => {
                const key = `${model.provider}:${model.model}`;
                const value = metric === 'tokens' ? model.total_tokens : model.cost_usd;
                const share = metric === 'tokens' ? model.token_share : model.cost_unknown ? null : model.cost_share;
                return <tr key={key}>
                  <td className="usage-rank">{index + 1}</td>
                  <td className="usage-col-model"><button type="button" className="usage-model-button" onClick={() => setSelectedModel(key)}><ProviderMark provider={model.provider} />{model.model}</button>
                    {value > 0 && peak > 0 && <div className="usage-row-bar" aria-hidden="true"><span style={{ width: `max(0.5rem, ${(value / peak) * 100}%)`, background: providerColor(model.provider) }} /></div>}</td>
                  <td>{model.cost_unknown ? <span className="usage-unpriced">Unpriced · <button type="button" onClick={() => setPriceTarget({ model: model.model })}>Set price</button></span> : formatUsd(model.cost_usd)}</td>
                  <td className="usage-col-share">{share === null ? '' : formatPercent(share)}</td>
                  <td>{formatTokens(model.total_tokens)}</td>
                </tr>;
              })}
            </tbody>
          </table> : <div className="usage-table-scroll" role="region" aria-label="Usage by period" tabIndex={0}><table className="usage-table">
            <thead><tr><th className="usage-col-model">{summary.range.resolution === 'hour' ? 'Hour' : 'Day'}</th>{activeProviders.map((name) => <th key={name}>{providerInfo(name).label}</th>)}<th>Total</th><th>Tokens</th></tr></thead>
            <tbody>
              {periods.length === 0 ? <tr><td colSpan={activeProviders.length + 3} className="usage-table-empty">No activity in this window.</td></tr> : periods.map((period) => <tr key={period.bucket}>
                <td className="usage-col-model">{formatBucket(period.bucket, summary.range.resolution, true)}</td>
                {activeProviders.map((name) => <td key={name}>{metric === 'cost' ? formatUsd(period.by_provider[name]?.cost_usd ?? 0) : formatTokens(period.by_provider[name]?.total_tokens ?? 0)}</td>)}
                <td className="usage-strong">{formatUsd(period.cost_usd)}</td><td>{formatTokens(period.total_tokens)}</td>
              </tr>)}
            </tbody>
          </table></div>}
        </section>

        <section className="usage-section">
          <div className="usage-section-head"><h2>Price overrides</h2><button type="button" className="secondary-button" onClick={() => setPriceTarget({ model: null })}>Add override</button></div>
          {Object.keys(overrides).length === 0 ? <p className="usage-muted">No overrides. Published prices from LiteLLM and OpenCode Zen are used; set a price for any model that is unpriced or priced differently for you.</p> :
            <ul className="usage-override-list">{Object.entries(overrides).map(([name, value]) => <li key={name}><strong>{name}</strong><span className="usage-muted">{formatRate(value.input)} in · {formatRate(value.output)} out · {formatRate(value.cache_read ?? value.input)} cache read · {formatRate(value.cache_write ?? value.input)} cache write per 1M</span><button type="button" className="secondary-button" onClick={() => setPriceTarget({ model: name })}>Edit</button></li>)}</ul>}
        </section>

        <p className="usage-footnote">Costs are estimates recomputed from current prices{pricingDate ? ` (LiteLLM, fetched ${pricingDate}` : ' ('}{pricing?.models_dev_fetched_at ? '; OpenCode Zen via models.dev' : ''}), so price updates and overrides apply to past usage. OpenRouter costs are provider-reported. Usage history starts when usage tracking was installed; unpriced models count as $0.</p>
      </div>}
    </div>
    {selected && summary && <ModelPanel model={selected} summary={summary} metric={metric} onClose={() => setSelectedModel(null)} onSetPrice={() => setPriceTarget({ model: selected.model })} />}
    {priceTarget && <PriceForm key={priceTarget.model ?? 'new'} model={priceTarget.model} current={priceModel?.rate_source !== 'override' ? priceModel?.rates ?? null : null} override={priceTarget.model ? overrides[priceTarget.model] : undefined}
      onClose={() => setPriceTarget(null)} onSaved={() => { setPriceTarget(null); void load(); }} />}
  </dialog>;
}

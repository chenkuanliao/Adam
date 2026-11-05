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
  series_cost: number[]; series_tokens: number[];
};
type ProviderTotals = Totals & { provider: string; cost_share: number; token_share: number };
type Period = { bucket: string; requests: number; cost_usd: number; total_tokens: number; by_provider: Record<string, { cost_usd: number; total_tokens: number; requests: number }> };
type PricingStatus = { litellm_fetched_at: string | null; models_dev_fetched_at: string | null; litellm_models: number; zen_models: number; stale: boolean; error: string | null; refresh_available_in: number };
type UsageSummary = {
  range: { days: number; resolution: 'day' | 'hour'; since: string; until: string; buckets: string[] };
  available_providers: string[]; totals: Totals; providers: ProviderTotals[]; models: ModelTotals[]; series: Period[]; pricing: PricingStatus;
};
type Metric = 'cost' | 'tokens';
type PriceOverride = { input: number; output: number; cache_read: number | null; cache_write: number | null };
type View = { kind: 'overview' } | { kind: 'model'; key: string } | { kind: 'price'; model: string | null; back: View };

const CHATGPT_USAGE_URL = 'https://chatgpt.com/#settings/Usage';
const RANGES = [{ days: 1, label: '24h' }, { days: 7, label: '7 days' }, { days: 30, label: '30 days' }, { days: 90, label: '90 days' }] as const;
const KINDS = [{ value: '', label: 'All requests' }, { value: 'chat', label: 'Chat' }, { value: 'quick_ask', label: 'Quick Ask' }, { value: 'title', label: 'Chat titles' }];
/** Fixed colors per provider (validated for color-vision deficiency), so filters never repaint a provider. */
const PROVIDERS: Record<string, { label: string; color: string; manageUrl?: string }> = {
  zen: { label: 'OpenCode Zen', color: '#3f7fc0' },
  openrouter: { label: 'OpenRouter', color: '#c4683f' },
  openai: { label: 'OpenAI', color: '#0f9480' },
  anthropic: { label: 'Anthropic', color: '#b8860b' },
  google: { label: 'Google', color: '#8a63c4' },
  chatgpt: { label: 'ChatGPT (subscription)', color: '#6f8f2a', manageUrl: CHATGPT_USAGE_URL },
};
const PROVIDER_ORDER = Object.keys(PROVIDERS);
const PREFERENCES_KEY = 'adam.usage.preferences';

const providerInfo = (provider: string) => PROVIDERS[provider] ?? { label: provider, color: '#7d7a72' };
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

function Dot({ color }: { color: string }) {
  return <i className="usage-dot" style={{ background: color }} aria-hidden="true" />;
}

/* Stacked area chart: one series per provider in fixed order, with a crosshair tooltip. */

type ChartSeries = { key: string; label: string; color: string; values: number[] };

function UsageAreaChart({ buckets, resolution, series, format, label, height = 200 }: {
  buckets: string[]; resolution: 'day' | 'hour'; series: ChartSeries[]; format: (value: number) => string; label: string; height?: number;
}) {
  const host = useRef<HTMLDivElement>(null);
  const [width, setWidth] = useState(560);
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
  const padding = { top: 10, right: 6, bottom: 24, left: 52 };
  const plotWidth = width - padding.left - padding.right;
  const plotHeight = height - padding.top - padding.bottom;
  const stacks = useMemo(() => stackSeries(series, buckets.length), [buckets, series]);
  const totals = stacks.length ? stacks[stacks.length - 1].upper : buckets.map(() => 0);
  const max = niceMax(Math.max(0, ...totals));
  const x = (index: number) => padding.left + (buckets.length <= 1 ? plotWidth / 2 : (index / (buckets.length - 1)) * plotWidth);
  const y = (value: number) => padding.top + plotHeight - (value / max) * plotHeight;
  const ticks = [0, 0.25, 0.5, 0.75, 1].map((step) => step * max);
  const labelEvery = Math.max(1, Math.ceil(buckets.length / Math.max(2, Math.floor(plotWidth / 76))));
  const empty = series.length === 0;

  function pointerMove(event: React.PointerEvent<SVGRectElement>) {
    const bounds = event.currentTarget.getBoundingClientRect();
    const ratio = (event.clientX - bounds.left) / Math.max(1, bounds.width);
    setHover(Math.min(buckets.length - 1, Math.max(0, Math.round(ratio * (buckets.length - 1)))));
  }

  return <div className="usage-chart" ref={host}>
    <svg width={width} height={height} role="img" aria-label={`${label}: ${series.map((item) => `${item.label} ${format(item.values.reduce((sum, value) => sum + value, 0))}`).join(', ') || 'no usage'}`}>
      {ticks.map((tick) => <g key={tick} className="usage-chart-grid"><line x1={padding.left} x2={width - padding.right} y1={y(tick)} y2={y(tick)} /><text x={padding.left - 8} y={y(tick)} dy="0.32em" textAnchor="end">{format(tick)}</text></g>)}
      {buckets.map((bucket, index) => (index % labelEvery === 0 && (buckets.length - 1 - index >= labelEvery * 0.75 || index === 0)) || index === buckets.length - 1
        ? <text key={bucket} className="usage-chart-axis" x={x(index)} y={height - 6} textAnchor={index === 0 ? 'start' : index === buckets.length - 1 ? 'end' : 'middle'}>{formatBucket(bucket, resolution)}</text> : null)}
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
    {empty && <p className="usage-chart-empty">No usage in this period</p>}
    {hover !== null && !empty && <div className="usage-tooltip" style={{ left: Math.min(Math.max(x(hover), 90), width - 90) }}>
      <strong>{formatBucket(buckets[hover], resolution, true)}</strong>
      {[...stacks].reverse().map((item) => <span key={item.key}><Dot color={item.color} />{item.label}<b>{format(item.values[hover] ?? 0)}</b></span>)}
      {stacks.length > 1 && <span className="usage-tooltip-total">Total<b>{format(totals[hover])}</b></span>}
    </div>}
    {series.length > 1 && <div className="usage-legend">{series.map((item) => <span key={item.key}><Dot color={item.color} />{item.label}</span>)}</div>}
  </div>;
}

/* Share bars for cost and tokens by type. */

type Segment = { label: string; value: number; color: string };
const TYPE_COLORS = { input: 'var(--usage-type-input)', cache_read: 'var(--usage-type-cache-read)', cache_write: 'var(--usage-type-cache-write)', output: 'var(--usage-type-output)', other: 'var(--usage-type-other)' };

const costSegments = (cost: CategoryCost): Segment[] => [
  { label: 'Input', value: cost.input, color: TYPE_COLORS.input },
  { label: 'Cache read', value: cost.cache_read, color: TYPE_COLORS.cache_read },
  { label: 'Cache write', value: cost.cache_write, color: TYPE_COLORS.cache_write },
  { label: 'Output', value: cost.output, color: TYPE_COLORS.output },
  { label: 'Provider total', value: cost.other, color: TYPE_COLORS.other },
];
const tokenSegments = (tokens: TokenTotals): Segment[] => [
  { label: 'Input', value: tokens.uncached_input, color: TYPE_COLORS.input },
  { label: 'Cache read', value: tokens.cache_read, color: TYPE_COLORS.cache_read },
  { label: 'Cache write', value: tokens.cache_write, color: TYPE_COLORS.cache_write },
  { label: 'Output', value: tokens.output, color: TYPE_COLORS.output },
];

function ShareBar({ label, segments, format, aside, emptyText = 'Nothing yet.' }: { label: string; segments: Segment[]; format: (value: number) => string; aside?: ReactNode; emptyText?: string }) {
  const visible = segments.filter((segment) => segment.value > 0);
  const total = visible.reduce((sum, segment) => sum + segment.value, 0);
  return <div className="usage-share">
    <div className="usage-share-head"><h4>{label}</h4>{aside}</div>
    {total > 0 ? <>
      <div className="usage-share-bar" role="img" aria-label={`${label}: ${visible.map((segment) => `${segment.label} ${format(segment.value)}`).join(', ')}`}>
        {visible.map((segment) => <span key={segment.label} title={`${segment.label} · ${format(segment.value)} · ${formatPercent(segment.value / total)}`} style={{ flexGrow: segment.value, background: segment.color }} />)}
      </div>
      <div className="usage-share-legend">{visible.map((segment) => <span key={segment.label}><Dot color={segment.color} />{segment.label}<b>{format(segment.value)}</b></span>)}</div>
    </> : <p className="usage-muted">{emptyText}</p>}
  </div>;
}

/* Model details and price form, shown in place inside the settings pane. */

const RATE_SOURCE_LABELS: Record<string, string> = { override: 'Your price', 'models.dev': 'OpenCode Zen price', litellm: 'Published price' };

function costPerMillion(model: ModelTotals): number | null {
  const priced = model.total_tokens - model.unpriced_tokens;
  return model.cost_unknown || priced <= 0 ? null : (model.cost_usd / priced) * 1e6;
}

function ModelDetail({ model, summary, metric, onBack, onSetPrice }: { model: ModelTotals; summary: UsageSummary; metric: Metric; onBack: () => void; onSetPrice: () => void }) {
  const hit = cacheHitRate(model.tokens);
  const perMillion = costPerMillion(model);
  const chartMetric = model.cost_unknown ? 'tokens' : metric;
  const info = providerInfo(model.provider);
  return <div className="usage-subview">
    <button type="button" className="usage-back-link" onClick={onBack}>‹ All usage</button>
    <div className="usage-subview-head">
      <div><h4><Dot color={info.color} />{model.model}</h4>
        <p>{info.label} · {model.requests} {model.requests === 1 ? 'request' : 'requests'}{model.cost_unknown ? '' : ` · ${formatPercent(model.cost_share)} of cost`}</p></div>
      <button type="button" className={model.cost_unknown ? 'primary-button' : 'secondary-button'} onClick={onSetPrice}>{model.rate_source === 'override' ? 'Edit price' : 'Set price'}</button>
    </div>
    <div className="usage-metrics">
      <div><span>Cost</span><strong>{model.cost_unknown ? 'Unpriced' : formatUsd(model.cost_usd)}</strong></div>
      <div><span>Tokens</span><strong>{formatTokens(model.total_tokens)}</strong></div>
      <div><span>Per 1M tokens</span><strong>{perMillion === null ? '—' : formatUsd(perMillion)}</strong></div>
      <div><span>Cache hit</span><strong>{hit === null ? '—' : formatPercent(hit)}</strong></div>
    </div>
    <section className="usage-card">
      <h4>{summary.range.resolution === 'hour' ? 'Hourly' : 'Daily'} {chartMetric === 'cost' ? 'cost' : 'tokens'}</h4>
      <UsageAreaChart label={`${model.model} ${chartMetric}`} buckets={summary.range.buckets} resolution={summary.range.resolution} height={170}
        format={chartMetric === 'cost' ? formatUsd : formatTokens}
        series={[{ key: model.model, label: model.model, color: info.color, values: chartMetric === 'cost' ? model.series_cost : model.series_tokens }]} />
    </section>
    <div className="usage-share-grid">
      {!model.cost_unknown && <ShareBar label="Cost by type" segments={costSegments(model.category_cost)} format={formatUsd} />}
      <ShareBar label="Tokens by type" segments={tokenSegments(model.tokens)} format={formatTokens} aside={model.tokens.reasoning > 0 ? <small className="usage-muted">incl. {formatTokens(model.tokens.reasoning)} reasoning</small> : undefined} />
    </div>
    <p className="usage-muted">{model.rates ? <>{RATE_SOURCE_LABELS[model.rate_source ?? ''] ?? 'Price'} per 1M tokens: {formatRate(model.rates.input)} input · {formatRate(model.rates.output)} output · {formatRate(model.rates.cache_read)} cache read · {formatRate(model.rates.cache_write)} cache write</> : model.cost_sources.providerReported ? 'Cost reported by the provider.' : 'No known price for this model. Set one to include it in the total.'}</p>
  </div>;
}

function PriceForm({ model, current, override, onBack, onSaved }: { model: string | null; current: PerMillion | null; override: PriceOverride | undefined; onBack: () => void; onSaved: () => void }) {
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

  async function send(method: 'PUT' | 'DELETE', event?: FormEvent) {
    event?.preventDefault();
    if (method === 'PUT' && !valid) return;
    setSaving(true); setStatus('');
    try {
      const response = method === 'PUT'
        ? await fetch('/api/usage/price-overrides', { method, headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ model: name.trim(), input: values[0], output: values[1], cache_read: values[2], cache_write: values[3] }) })
        : await fetch(`/api/usage/price-overrides?model=${encodeURIComponent(name.trim())}`, { method });
      if (!response.ok) throw new Error((await response.json().catch(() => ({}))).detail ?? 'Could not save the price.');
      onSaved();
    } catch (reason) {
      setStatus(reason instanceof Error ? reason.message : 'Could not save the price.');
    } finally { setSaving(false); }
  }
  const money = (label: string, value: string, change: (value: string) => void, placeholder: string) =>
    <label className="field-label">{label}<span className="usage-money-input"><i>$</i><input type="number" inputMode="decimal" min="0" step="any" value={value} placeholder={placeholder} onChange={(event) => change(event.target.value)} /></span></label>;
  return <form className="usage-subview" onSubmit={(event) => void send('PUT', event)}>
    <button type="button" className="usage-back-link" onClick={onBack}>‹ Back</button>
    <div className="usage-subview-head"><div><h4>{override ? 'Edit model price' : 'Set model price'}</h4>
      <p>USD per 1M tokens. Your price replaces published prices and applies to all past usage of this model.</p></div></div>
    <label className="field-label">Model ID<input value={name} onChange={(event) => setName(event.target.value)} disabled={model !== null} placeholder="e.g. gpt-5.6-terra" autoFocus={model === null} /></label>
    <div className="usage-price-grid">
      {money('Input', input, setInput, '0.00')}
      {money('Output', output, setOutput, '0.00')}
      {money('Cache read (optional)', cacheRead, setCacheRead, 'Same as input')}
      {money('Cache write (optional)', cacheWrite, setCacheWrite, 'Same as input')}
    </div>
    {!override && current && <p className="usage-muted">Filled in with the current published price.</p>}
    <div className="usage-form-actions">
      <span role="status" className="usage-error">{status}</span>
      {override && <button type="button" className="secondary-button" onClick={() => void send('DELETE')} disabled={saving}>Remove my price</button>}
      <button type="submit" className="primary-button" disabled={!valid || saving}>{saving ? 'Saving…' : 'Save price'}</button>
    </div>
  </form>;
}

/* The Usage section of Settings. */

export default function UsageSettings() {
  const [preferences, setPreferences] = useState<{ days: number; metric: Metric }>(readPreferences);
  const [provider, setProvider] = useState('');
  const [kind, setKind] = useState('');
  const [breakdown, setBreakdown] = useState<'model' | 'day'>('model');
  const [summary, setSummary] = useState<UsageSummary | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [refreshing, setRefreshing] = useState(false);
  const [view, setView] = useState<View>({ kind: 'overview' });
  const [overrides, setOverrides] = useState<Record<string, PriceOverride>>({});
  const request = useRef(0);
  const root = useRef<HTMLDivElement>(null);
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
      const params = new URLSearchParams({ days: String(days), tz: Intl.DateTimeFormat().resolvedOptions().timeZone });
      if (provider) params.set('provider', provider);
      if (kind) params.set('kind', kind);
      const [response, overrideResponse] = await Promise.all([fetch(`/api/usage/summary?${params}`), fetch('/api/usage/price-overrides')]);
      if (!response.ok) throw new Error('Could not load usage. Try again.');
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

  // Escape steps back to the overview before it closes Settings.
  useEffect(() => {
    if (view.kind === 'overview') return;
    const back = (event: KeyboardEvent) => {
      if (event.key !== 'Escape' || document.querySelector('.app-select-menu')) return;
      event.preventDefault();
      setView((current) => current.kind === 'price' ? current.back : { kind: 'overview' });
    };
    window.addEventListener('keydown', back, true);
    return () => window.removeEventListener('keydown', back, true);
  }, [view]);
  const go = (next: View) => { setView(next); root.current?.closest('.provider-content')?.scrollTo({ top: 0 }); };

  async function refreshPrices() {
    setRefreshing(true); setNotice('');
    try {
      const response = await fetch('/api/usage/prices/refresh', { method: 'POST' });
      const value = await response.json().catch(() => ({}));
      if (response.status === 429) setNotice(value.detail ?? 'Prices were updated a moment ago.');
      else if (!response.ok) throw new Error('Could not update prices.');
      else setNotice(value.error ? 'Some price lists could not be reached; the last saved prices are still used.' : 'Prices updated.');
      await load();
    } catch (reason) {
      setNotice(reason instanceof Error ? reason.message : 'Could not update prices.');
    } finally { setRefreshing(false); }
  }

  const totals = summary?.totals;
  const activeProviders = useMemo(() => sortProviders(summary?.providers.map((item) => item.provider) ?? []), [summary]);
  const chartSeries = useMemo<ChartSeries[]>(() => !summary ? [] : activeProviders.map((name) => ({
    key: name, label: providerInfo(name).label, color: providerInfo(name).color,
    values: summary.series.map((period) => metric === 'cost' ? period.by_provider[name]?.cost_usd ?? 0 : period.by_provider[name]?.total_tokens ?? 0),
  })), [summary, activeProviders, metric]);
  const models = useMemo(() => [...(summary?.models ?? [])].sort((a, b) => metric === 'tokens' ? b.total_tokens - a.total_tokens : (Number(a.cost_unknown) - Number(b.cost_unknown)) || b.cost_usd - a.cost_usd || b.total_tokens - a.total_tokens), [summary, metric]);
  const peak = Math.max(0, ...models.map((model) => metric === 'tokens' ? model.total_tokens : model.cost_usd));
  const periods = useMemo(() => [...(summary?.series ?? [])].filter((period) => period.requests > 0).reverse(), [summary]);
  const pricing = summary?.pricing;
  const pricingDate = pricing?.litellm_fetched_at ? new Date(pricing.litellm_fetched_at).toLocaleString('en-US', { month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' }) : null;
  const providerOptions = [{ value: '', label: 'All providers' }, ...sortProviders([...new Set([...(summary?.available_providers ?? []), ...(provider ? [provider] : [])])]).map((name) => ({ value: name, label: providerInfo(name).label }))];
  const unpricedShare = totals && totals.requests ? totals.unpriced_requests / totals.requests : 0;

  const selectedModel = view.kind === 'model' ? summary?.models.find((model) => `${model.provider}:${model.model}` === view.key) : undefined;
  const priceModel = view.kind === 'price' && view.model ? summary?.models.find((model) => model.model === view.model) : undefined;

  let body: ReactNode;
  if (view.kind === 'model' && selectedModel && summary) {
    body = <ModelDetail model={selectedModel} summary={summary} metric={metric} onBack={() => go({ kind: 'overview' })} onSetPrice={() => go({ kind: 'price', model: selectedModel.model, back: view })} />;
  } else if (view.kind === 'price') {
    body = <PriceForm key={view.model ?? 'new'} model={view.model} current={priceModel && priceModel.rate_source !== 'override' ? priceModel.rates : null} override={view.model ? overrides[view.model] : undefined}
      onBack={() => go(view.back)} onSaved={() => { go(view.back); void load(); }} />;
  } else if (!summary || !totals) {
    body = <div className="model-empty">{loading ? 'Loading usage…' : error || 'Usage is unavailable.'}</div>;
  } else if (totals.requests === 0 && !provider && !kind) {
    body = <div className="usage-empty"><strong>No usage in this period</strong><p>Ask a question in any paper and its token usage and estimated cost appear here. Usage is recorded from when this feature was installed; earlier requests weren’t measured.</p></div>;
  } else {
    body = <div className={`usage-overview${loading ? ' is-loading' : ''}`}>
      <section className="usage-card usage-hero">
        <div className="usage-hero-summary">
          <span className="usage-kicker">{metric === 'cost' ? 'Estimated cost' : 'Processed tokens'}</span>
          <span className="usage-hero-figure">{metric === 'cost' ? formatUsd(totals.cost_usd) : formatTokens(totals.total_tokens)}</span>
          <span className="usage-muted">{totals.requests.toLocaleString()} {totals.requests === 1 ? 'request' : 'requests'}
            {metric === 'cost' && unpricedShare > 0 && <span className="usage-flag" title="Requests to models without a known price count as $0.">{formatPercent(unpricedShare)} unpriced</span>}</span>
          <ul className="usage-provider-list">
            {[...summary.providers].sort((a, b) => metric === 'tokens' ? b.total_tokens - a.total_tokens : b.cost_usd - a.cost_usd).map((item) => {
              const info = providerInfo(item.provider);
              return <li key={item.provider}>
                <span className="usage-provider-name"><Dot color={info.color} />{info.label}</span>
                <strong>{metric === 'cost' ? formatUsd(item.cost_usd) : formatTokens(item.total_tokens)}</strong>
                <span className="usage-muted usage-provider-sub">{item.requests} {item.requests === 1 ? 'request' : 'requests'} · {metric === 'cost' ? `${formatPercent(item.cost_share)} of cost` : `${formatPercent(item.token_share)} of tokens`}
                  {info.manageUrl && <> · <a href={info.manageUrl} target="_blank" rel="noreferrer">Manage usage ↗</a></>}</span>
              </li>;
            })}
          </ul>
        </div>
        <div className="usage-hero-chart">
          <h4>{summary.range.resolution === 'hour' ? 'Hourly' : 'Daily'} {metric === 'cost' ? 'cost' : 'tokens'}</h4>
          <UsageAreaChart label={`${summary.range.resolution === 'hour' ? 'Hourly' : 'Daily'} ${metric}`} buckets={summary.range.buckets} resolution={summary.range.resolution} series={chartSeries} format={metric === 'cost' ? formatUsd : formatTokens} />
        </div>
      </section>

      <div className="usage-metrics">
        <div title="Uncached input + cache reads + cache writes + output"><span>Processed tokens</span><strong>{formatTokens(totals.total_tokens)}</strong></div>
        <div><span>Cached input</span><strong>{formatTokens(totals.tokens.cache_read)}</strong></div>
        <div title="Includes cache writes"><span>Uncached input</span><strong>{formatTokens(totals.tokens.uncached_input + totals.tokens.cache_write)}</strong></div>
        <div title={`Includes ${formatTokens(totals.tokens.reasoning)} reasoning tokens`}><span>Output</span><strong>{formatTokens(totals.tokens.output)}</strong></div>
        <div title="Cache reads × (input price − cache read price)"><span>Cache savings</span><strong>{formatUsd(totals.cache_savings_usd)}</strong></div>
      </div>

      {totals.total_tokens > 0 && <div className="usage-share-grid">
        <ShareBar label="Cost by type" segments={costSegments(totals.category_cost)} format={formatUsd} emptyText={totals.unpriced_requests ? 'No cost yet: these requests used free or unpriced models.' : 'No cost yet: these requests used free models.'} />
        <ShareBar label="Tokens by type" segments={tokenSegments(totals.tokens)} format={formatTokens} />
      </div>}

      <section className="usage-breakdown">
        <div className="usage-section-head"><h4>Breakdown</h4>
          <div className="prompt-kind-tabs" role="group" aria-label="Breakdown">
            <button type="button" className={breakdown === 'model' ? 'active' : ''} aria-pressed={breakdown === 'model'} onClick={() => setBreakdown('model')}>By model</button>
            <button type="button" className={breakdown === 'day' ? 'active' : ''} aria-pressed={breakdown === 'day'} onClick={() => setBreakdown('day')}>By {summary.range.resolution === 'hour' ? 'hour' : 'day'}</button>
          </div></div>
        {breakdown === 'model' ? <table className="usage-table">
          <thead><tr><th>#</th><th className="usage-col-main">Model</th><th>Cost</th><th className="usage-col-share">Share</th><th>Tokens</th></tr></thead>
          <tbody>
            {models.length === 0 ? <tr><td colSpan={5} className="usage-table-empty">No requests match these filters.</td></tr> : models.map((model, index) => {
              const key = `${model.provider}:${model.model}`;
              const value = metric === 'tokens' ? model.total_tokens : model.cost_usd;
              const share = metric === 'tokens' ? model.token_share : model.cost_unknown ? null : model.cost_share;
              const color = providerInfo(model.provider).color;
              return <tr key={key}>
                <td className="usage-rank">{index + 1}</td>
                <td className="usage-col-main"><button type="button" className="usage-model-button" onClick={() => go({ kind: 'model', key })}><Dot color={color} />{model.model}</button>
                  {value > 0 && peak > 0 && <div className="usage-row-bar" aria-hidden="true"><span style={{ width: `max(0.5rem, ${(value / peak) * 100}%)`, background: color }} /></div>}</td>
                <td>{model.cost_unknown ? <span className="usage-unpriced">Unpriced · <button type="button" onClick={() => go({ kind: 'price', model: model.model, back: { kind: 'overview' } })}>Set price</button></span> : formatUsd(model.cost_usd)}</td>
                <td className="usage-col-share">{share === null ? '' : formatPercent(share)}</td>
                <td>{formatTokens(model.total_tokens)}</td>
              </tr>;
            })}
          </tbody>
        </table> : <div className="usage-table-scroll" role="region" aria-label="Usage by period" tabIndex={0}><table className="usage-table">
          <thead><tr><th className="usage-col-main">{summary.range.resolution === 'hour' ? 'Hour' : 'Day'}</th>{activeProviders.map((name) => <th key={name}>{providerInfo(name).label}</th>)}<th>Total</th><th>Tokens</th></tr></thead>
          <tbody>
            {periods.length === 0 ? <tr><td colSpan={activeProviders.length + 3} className="usage-table-empty">No requests match these filters.</td></tr> : periods.map((period) => <tr key={period.bucket}>
              <td className="usage-col-main">{formatBucket(period.bucket, summary.range.resolution, true)}</td>
              {activeProviders.map((name) => <td key={name}>{metric === 'cost' ? formatUsd(period.by_provider[name]?.cost_usd ?? 0) : formatTokens(period.by_provider[name]?.total_tokens ?? 0)}</td>)}
              <td className="usage-strong">{formatUsd(period.cost_usd)}</td><td>{formatTokens(period.total_tokens)}</td>
            </tr>)}
          </tbody>
        </table></div>}
      </section>

      <section className="usage-breakdown">
        <div className="usage-section-head"><h4>Your model prices</h4><button type="button" className="secondary-button" onClick={() => go({ kind: 'price', model: null, back: { kind: 'overview' } })}>Add price</button></div>
        {Object.keys(overrides).length === 0 ? <p className="usage-muted">None. Published prices are used; add your own for a model that’s missing or priced differently for you.</p> :
          <ul className="usage-override-list">{Object.entries(overrides).map(([name, value]) => <li key={name}><strong>{name}</strong><span className="usage-muted">{formatRate(value.input)} input · {formatRate(value.output)} output per 1M</span><button type="button" className="secondary-button" onClick={() => go({ kind: 'price', model: name, back: { kind: 'overview' } })}>Edit</button></li>)}</ul>}
      </section>

      <p className="usage-footnote">Costs are estimates from current published prices{pricingDate ? ` (updated ${pricingDate})` : ''}; OpenRouter costs come from OpenRouter. Usage is recorded from when this feature was installed.</p>
    </div>;
  }

  return <div className="usage-settings" ref={root}>
    <div className="provider-heading">
      <div><h3>Usage &amp; cost</h3><p>Token usage and estimated spend for chats, Quick Ask, and chat titles.</p></div>
      <button type="button" className="secondary-button" onClick={() => void refreshPrices()} disabled={refreshing} title={pricingDate ? `Prices last updated ${pricingDate}` : undefined}>{refreshing ? 'Updating…' : 'Update prices'}</button>
    </div>
    {view.kind === 'overview' && <div className="usage-controls" role="toolbar" aria-label="Usage filters">
      <div className="prompt-kind-tabs" role="group" aria-label="Time range">
        {RANGES.map((range) => <button type="button" key={range.days} className={days === range.days ? 'active' : ''} aria-pressed={days === range.days} onClick={() => updatePreferences({ days: range.days })}>{range.label}</button>)}
      </div>
      <div className="prompt-kind-tabs" role="group" aria-label="Metric">
        {(['cost', 'tokens'] as const).map((value) => <button type="button" key={value} className={metric === value ? 'active' : ''} aria-pressed={metric === value} onClick={() => updatePreferences({ metric: value })}>{value === 'cost' ? 'Cost' : 'Tokens'}</button>)}
      </div>
      <AppSelect label="Provider" value={provider} onChange={setProvider} options={providerOptions} />
      <AppSelect label="Request type" value={kind} onChange={setKind} options={KINDS} />
    </div>}
    {(notice || (error && summary) || pricing?.error) && view.kind === 'overview' && <p className="usage-notice" role="status">{notice || error || 'Price lists could not be reached; the last saved prices are used.'}</p>}
    {body}
  </div>;
}

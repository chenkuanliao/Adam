import assert from 'node:assert/strict';
import test from 'node:test';
import { cacheHitRate, formatPercent, formatTokens, formatUsd, niceMax, stackSeries } from '../app/usage-format';

test('formats money, tokens, and shares compactly', () => {
  assert.equal(formatUsd(0), '$0.00');
  assert.equal(formatUsd(0.004), '<$0.01');
  assert.equal(formatUsd(12.345), '$12.35');
  assert.equal(formatUsd(1234.5), '$1,235');
  assert.equal(formatTokens(950), '950');
  assert.equal(formatTokens(1500), '1.5K');
  assert.equal(formatTokens(2_400_000), '2.4M');
  assert.equal(formatTokens(34_000_000), '34M');
  assert.equal(formatPercent(0.004), '<1%');
  assert.equal(formatPercent(0.456), '46%');
});

test('axis maximum rounds up to a readable step', () => {
  assert.equal(niceMax(0), 1);
  assert.equal(niceMax(3.2), 4);
  assert.equal(niceMax(0.38), 0.4);
  assert.equal(niceMax(0.18), 0.2);
  assert.equal(niceMax(2_100_000), 3_000_000);
});

test('stacks series in order without mutating input', () => {
  const series = [{ key: 'zen', values: [1, 2, 0] }, { key: 'anthropic', values: [3, 0, 4] }];
  const stacked = stackSeries(series, 3);
  assert.deepEqual(stacked[0].lower, [0, 0, 0]);
  assert.deepEqual(stacked[1].lower, [1, 2, 0]);
  assert.deepEqual(stacked[1].upper, [4, 2, 4]);
  assert.deepEqual(series[0], { key: 'zen', values: [1, 2, 0] });
});

test('cache hit rate counts cache writes as misses', () => {
  assert.equal(cacheHitRate({ uncached_input: 0, cache_read: 0, cache_write: 0 }), null);
  assert.equal(cacheHitRate({ uncached_input: 100, cache_read: 600, cache_write: 300 }), 0.6);
});

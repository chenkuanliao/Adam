import assert from 'node:assert/strict';
import { test } from 'node:test';
import fs from 'node:fs';
import ts from 'typescript';

// Use the app's TypeScript source with Node's test runner; no browser type globals.
async function load(relative) {
  const code = ts.transpileModule(fs.readFileSync(new URL(relative, import.meta.url), 'utf8'), { compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 } }).outputText;
  return import(`data:text/javascript;base64,${Buffer.from(code).toString('base64')}`);
}
const { parseControlNumber } = await load('../app/reader-controls.ts');
const { readingHistory, saveReadingPosition, forgetReadingPosition } = await load('../app/reading-history.ts');

test('page destinations reject incomplete, fractional, and out-of-document input', () => {
  for (const raw of ['', ' ', 'abc', '2x', '1e2', '1.5', '0', '-1', '8', 'Infinity', '3%']) assert.equal(parseControlNumber(raw, 1, 7, true), null, raw);
  assert.equal(parseControlNumber(' 3 ', 1, 7, true), 3);
  assert.equal(parseControlNumber('7', 1, 7, true), 7);
});
test('zoom accepts percentages and decimals within the supported bounds', () => {
  assert.equal(parseControlNumber('125%', 10, 3200), 125);
  assert.equal(parseControlNumber('137.5', 10, 3200), 137.5);
  for (const raw of ['0', '9', '3201', '100%%', '-20%', 'NaN', '1/2']) assert.equal(parseControlNumber(raw, 10, 3200), null, raw);
  assert.equal(parseControlNumber('99', 100, 200), null);
});
test('reading history preserves page, zoom, and offsets; deduplicates and prunes', () => {
  const storage = new Map();
  globalThis.localStorage = { getItem: (key) => storage.get(key) ?? null, setItem: (key, value) => storage.set(key, value) };
  const record = (id, readAt) => ({ id, page: 3, offset: .42, horizontal: .25, zoom: 1.5, fit: false, readAt });
  for (let i = 0; i < 40; i++) saveReadingPosition(record(`paper-${i}`, i));
  assert.equal(readingHistory().length, 30);
  saveReadingPosition(record('paper-20', 100));
  assert.deepEqual(readingHistory()[0], record('paper-20', 100));
  assert.equal(readingHistory().filter((item) => item.id === 'paper-20').length, 1);
  forgetReadingPosition('paper-20');
  assert.equal(readingHistory().some((item) => item.id === 'paper-20'), false);
  storage.set('adam.readingHistory.v1', '{broken');
  assert.deepEqual(readingHistory(), []);
  storage.set('adam.readingHistory.v1', JSON.stringify([record('good', 100), { ...record('bad', 2), zoom: -1 }, null, { ...record('bad-page', 4), page: 0 }]));
  assert.deepEqual(readingHistory(), [record('good', 100)]);
  globalThis.localStorage = { getItem() { throw Error('blocked'); }, setItem() { throw Error('full'); } };
  assert.deepEqual(readingHistory(), []);
  assert.doesNotThrow(() => saveReadingPosition(record('x', 1)));
});

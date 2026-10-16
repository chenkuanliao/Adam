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

const { sameHighlightLocation } = await load('../app/highlight-location.ts');
test('identical words at different positions remain independent highlights', () => {
  const selected = { page: 1, text: 'review', rects: [{ left: .1, top: .2, width: .04, height: .015 }] };
  assert.equal(sameHighlightLocation(selected, structuredClone(selected)), true);
  assert.equal(sameHighlightLocation(selected, { ...selected, page: 2 }), false);
  for (const shift of [{ left: .3 }, { top: .4 }, { left: .145 }, { width: .08 }]) {
    assert.equal(sameHighlightLocation(selected, { ...selected, rects: [{ ...selected.rects[0], ...shift }] }), false);
  }
  assert.equal(sameHighlightLocation(selected, { ...selected, rects: [{ ...selected.rects[0], left: .1001, top: .2001 }] }), true);
  assert.equal(sameHighlightLocation(selected, { ...selected, rects: [] }), false);
  assert.equal(sameHighlightLocation(selected, { ...selected, rects: [{ ...selected.rects[0], width: NaN }] }), false);
});
test('multiline highlights must match all line locations', () => {
  const first = { left: .1, top: .2, width: .3, height: .015 };
  const second = { left: .1, top: .23, width: .2, height: .015 };
  const selected = { page: 1, text: 'repeated passage', rects: [first, second] };
  assert.equal(sameHighlightLocation(selected, { ...selected, rects: [second, first] }), true);
  assert.equal(sameHighlightLocation(selected, { ...selected, rects: [first] }), false);
  assert.equal(sameHighlightLocation(selected, { ...selected, rects: [first, { ...second, top: .5 }] }), false);
});

const { createTextStore, createStreamBatcher } = await load('../app/stream-text.ts');
function fakeTimers() {
  const pending = new Map();
  let next = 1;
  return {
    timers: { setTimeout: (callback, delay) => { pending.set(next, { callback, delay }); return next++; }, clearTimeout: (handle) => pending.delete(handle) },
    pending,
    tick() { const due = [...pending]; pending.clear(); for (const [, { callback }] of due) callback(); },
  };
}
// Mirrors the reader's stream loop: accumulate every fragment, publish the
// first one immediately, batch the rest, flush on EOF/error, cancel when done.
async function consumeStream(fragments, { store, clock, failAfter = Infinity }) {
  let text = '';
  let publishes = 0;
  const batcher = createStreamBatcher(() => { publishes++; store.set(text); }, 50, clock.timers);
  try {
    for (const [index, fragment] of fragments.entries()) {
      if (index >= failAfter) throw new Error('stream failed');
      const first = !text;
      text += fragment;
      if (first) batcher.flush(); else batcher.schedule();
      if (index % 10 === 9) clock.tick();
    }
    batcher.flush();
  } catch (reason) {
    batcher.flush();
    return { text, publishes, error: reason.message };
  } finally {
    batcher.cancel();
  }
  return { text, publishes };
}

test('text stores notify subscribers only for real changes', () => {
  const store = createTextStore();
  let calls = 0;
  const unsubscribe = store.subscribe(() => calls++);
  store.set('a'); store.set('a'); store.set('ab');
  assert.equal(store.get(), 'ab');
  assert.equal(calls, 2);
  unsubscribe();
  store.set('abc');
  assert.equal(calls, 2);
});
test('stream batcher coalesces fragments, flushes immediately, and stops after cancel', () => {
  const clock = fakeTimers();
  let published = 0;
  const batcher = createStreamBatcher(() => published++, 50, clock.timers);
  for (let i = 0; i < 25; i++) batcher.schedule();
  assert.equal(clock.pending.size, 1);
  assert.equal([...clock.pending.values()][0].delay, 50);
  clock.tick();
  assert.equal(published, 1);
  batcher.schedule();
  batcher.flush();
  assert.equal(published, 2);
  assert.equal(clock.pending.size, 0, 'flush clears the scheduled publish');
  batcher.schedule();
  batcher.cancel();
  clock.tick();
  batcher.flush();
  batcher.schedule();
  assert.equal(published, 2, 'cancelled batchers never publish (unmount / finished stream)');
  assert.equal(clock.pending.size, 0);
});
test('mocked stream publishes the final fragment at EOF with bounded updates', async () => {
  const store = createTextStore();
  const clock = fakeTimers();
  let renders = 0;
  store.subscribe(() => renders++);
  const fragments = Array.from({ length: 200 }, (_, i) => `tok${i} `);
  const result = await consumeStream(fragments, { store, clock });
  assert.equal(store.get(), fragments.join(''), 'last fragment is visible without waiting for a timer');
  assert.equal(result.publishes, 1 + 20 + 1, 'first fragment + one per batch window + EOF flush');
  assert.ok(renders <= result.publishes);
  assert.equal(clock.pending.size, 0, 'no timer survives the stream');
});
test('mocked stream error still shows every received fragment', async () => {
  const store = createTextStore();
  const clock = fakeTimers();
  const result = await consumeStream(['$x^2$ ', 'and ', 'more'], { store, clock, failAfter: 2 });
  assert.equal(result.error, 'stream failed');
  assert.equal(store.get(), '$x^2$ and ');
  assert.equal(clock.pending.size, 0);
});

// TSX loader: bare imports resolve against frontend/node_modules.
async function loadTsx(relative) {
  const source = fs.readFileSync(new URL(relative, import.meta.url), 'utf8');
  const code = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX } }).outputText
    .replace(/(from\s*|import\s*)(['"])([^'"./][^'"]*)\2/g, (_match, keyword, quote, specifier) => `${keyword}${quote}${import.meta.resolve(specifier)}${quote}`);
  return import(`data:text/javascript;base64,${Buffer.from(code).toString('base64')}`);
}
const React = await import('react');
const { renderToStaticMarkup } = await import('react-dom/server');
const { MarkdownAnswer, normalizeMathDelimiters, prepareAnswerMarkdown } = await loadTsx('../app/chat-text.tsx');

test('answer markdown keeps math, code, and web citation rendering', () => {
  const web = { sources: [{ url: 'https://example.test/a>b' }] };
  const answer = 'Energy \\(E=mc^2\\) per [W1] and [W2].\n\n\\[\\int_0^1 x\\,dx\\]\n\n```py\nprint("\\(not math\\)")\n```';
  assert.equal(prepareAnswerMarkdown(answer, null), normalizeMathDelimiters(answer));
  const html = renderToStaticMarkup(React.createElement(MarkdownAnswer, { web, streaming: true }, answer));
  assert.match(html, /class="katex"/);
  assert.match(html, /class="katex-display"/);
  assert.match(html, /<code class="language-py">print\(&quot;\\\(not math\\\)&quot;\)/);
  assert.match(html, /<a href="https:\/\/example.test\/a%3Eb" target="_blank" rel="noreferrer">\[W1\]<\/a>/);
  assert.doesNotMatch(html, /node=/);
  assert.match(html, /\[W2\]/);
  assert.match(html, /class="stream-cursor"/);
});
test('saved answers are memoized so unchanged history is not re-parsed', () => {
  assert.equal(MarkdownAnswer.$$typeof, Symbol.for('react.memo'));
  assert.equal(MarkdownAnswer.compare ?? null, null, 'default shallow comparison: same answer string + web object skips rendering');
});

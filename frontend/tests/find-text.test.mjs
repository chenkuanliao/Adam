import assert from 'node:assert/strict';
import { test } from 'node:test';
import fs from 'node:fs';
import ts from 'typescript';
const code = ts.transpileModule(fs.readFileSync(new URL('../app/find-text.ts', import.meta.url), 'utf8'), { compilerOptions: { module: ts.ModuleKind.ESNext } }).outputText;
const { findOffsets, findMatches } = await import(`data:text/javascript;base64,${Buffer.from(code).toString('base64')}`);
test('find is case insensitive, literal, and returns every nonoverlapping match', () => {
  assert.deepEqual(findOffsets('Attention and ATTENTION', 'attention'), [0, 14]);
  assert.deepEqual(findOffsets('a+b aab a+b', 'a+b'), [0, 8]);
  assert.deepEqual(findOffsets('100% a_b [x]', '[x]'), [9]);
  assert.deepEqual(findOffsets('anything', '   '), []);
  assert.deepEqual(findOffsets('anything', 'missing'), []);
});
test('phrases match PDF line breaks and preserve original range offsets', () => {
  assert.deepEqual(findMatches('sparse\n  attention and sparse attention', ' sparse attention '), [{ start: 0, end: 18 }, { start: 23, end: 39 }]);
});

const highlightCode = ts.transpileModule(fs.readFileSync(new URL('../app/search-highlights.ts', import.meta.url), 'utf8'), { compilerOptions: { module: ts.ModuleKind.ESNext } }).outputText;
const { createSearchHighlights } = await import(`data:text/javascript;base64,${Buffer.from(highlightCode).toString('base64')}`);
test('typing, backspacing, and clearing release old ranges and reuse two highlight objects', () => {
  const registry = new Map();
  const allocated = [];
  const painter = createSearchHighlights(registry, () => { const highlight = new Set(); allocated.push(highlight); return highlight; }, 'pdf');
  const text = 'Synthetic study. A study of systems.';
  for (const query of ['s', 'st', 'stu', 'stud', 'study', 'stud', 's', 'missing', '']) {
    const ranges = findMatches(text, query).map(({ start, end }) => ({ text: text.slice(start, end) }));
    painter.replace(ranges, ranges[0]);
    assert.deepEqual([...registry.get('find-pdf')], ranges);
    assert.deepEqual([...registry.get('find-pdf-active')], ranges.slice(0, 1));
    assert.equal(allocated.length, 2);
    painter.clear();
    assert.equal(allocated[0].size, 0);
    assert.equal(allocated[1].size, 0);
  }
  painter.dispose();
  assert.equal(registry.size, 0);
  assert.ok(allocated.every((item) => item.size === 0));
});

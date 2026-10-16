import assert from 'node:assert/strict';
import { test } from 'node:test';
import fs from 'node:fs';
import ts from 'typescript';

async function load(relative) {
  const code = ts.transpileModule(fs.readFileSync(new URL(relative, import.meta.url), 'utf8'), { compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 } }).outputText;
  return import(`data:text/javascript;base64,${Buffer.from(code).toString('base64')}`);
}
const { DEFAULT_PAGE_ASPECT, MAX_RASTER_PIXELS, PAGE_OVERSCAN, isPageMounted, mountedPageWindow, pageAspects, rasterScale, sameWindow, visiblePageRange } = await load('../app/page-window.ts');

// Stack pages like the reader: per-page heights with a fixed gap between them.
function layout(heights, gap = 28, top = 32) {
  let y = top;
  return heights.map((height) => { const rect = { top: y, bottom: y + height }; y += height + gap; return rect; });
}

test('placeholder aspects keep mixed page sizes and estimate unknown pages', () => {
  assert.deepEqual(pageAspects([], 3), [DEFAULT_PAGE_ASPECT, DEFAULT_PAGE_ASPECT, DEFAULT_PAGE_ASPECT]);
  // Landscape page 2 stays landscape; unknown pages borrow the nearest earlier known size.
  assert.deepEqual(pageAspects([1.4, .7, undefined, 1.3, undefined], 5), [1.4, .7, .7, 1.3, 1.3]);
  // Before page 1 is known, the first known page is a better estimate than Letter.
  assert.deepEqual(pageAspects([undefined, undefined, .75], 4), [.75, .75, .75, .75]);
  assert.deepEqual(pageAspects([0, -1, Number.NaN], 3).every((aspect) => aspect === DEFAULT_PAGE_ASPECT), true);
});

test('visible pages are found by binary search across mixed heights and gaps', () => {
  const rects = layout([1000, 500, 1000, 1000, 300, 1000]);
  let reads = 0;
  const rectOf = (index) => { reads++; return rects[index]; };
  assert.deepEqual(visiblePageRange(rects.length, rectOf, 0, 900), { start: 1, end: 1 });
  // A view straddling the 1/2 boundary includes both pages for cross-page selection.
  assert.deepEqual(visiblePageRange(rects.length, rectOf, 900, 1100), { start: 1, end: 2 });
  // Inside the gap between pages 2 and 3 we keep the next page.
  assert.deepEqual(visiblePageRange(rects.length, rectOf, rects[1].bottom + 5, rects[1].bottom + 10), { start: 3, end: 3 });
  assert.deepEqual(visiblePageRange(rects.length, rectOf, rects[3].top + 10, rects[5].top + 10), { start: 4, end: 6 });
  // Past the end clamps to the last page; an empty document has no range.
  assert.deepEqual(visiblePageRange(rects.length, rectOf, 1e6, 1e6 + 900), { start: 6, end: 6 });
  assert.equal(visiblePageRange(0, rectOf, 0, 100), null);

  const many = layout(Array.from({ length: 3000 }, (_, index) => index % 7 === 0 ? 1600 : 1100));
  reads = 0;
  const middle = visiblePageRange(many.length, (index) => { reads++; return many[index]; }, many[1500].top + 1, many[1501].bottom - 1);
  assert.deepEqual(middle, { start: 1501, end: 1502 });
  assert.ok(reads < 40, `expected logarithmic reads, saw ${reads}`);
});

test('mounted window adds bounded overscan, region-capture extension, and pinned selection endpoints', () => {
  const range = (window) => window && { start: window.start, end: window.end };
  assert.deepEqual(mountedPageWindow({ start: 10, end: 11 }, 300), { start: 10 - PAGE_OVERSCAN, end: 11 + PAGE_OVERSCAN, pinned: [] });
  assert.deepEqual(range(mountedPageWindow({ start: 1, end: 1 }, 300, { overscan: 2 })), { start: 1, end: 3 });
  assert.deepEqual(range(mountedPageWindow({ start: 299, end: 300 }, 300, { overscan: 2 })), { start: 297, end: 300 });
  assert.deepEqual(range(mountedPageWindow(null, 5, { overscan: 2 })), { start: 1, end: 3 });
  assert.equal(mountedPageWindow(null, 0), null);
  // A selection from page 40 to 60 keeps only its endpoint pages, not the 19 between.
  const selecting = mountedPageWindow({ start: 60, end: 60 }, 300, { overscan: 2, pinned: [40, 60, 40, 999, 0] });
  assert.deepEqual(selecting, { start: 58, end: 62, pinned: [40] });
  assert.equal(isPageMounted(selecting, 40), true);
  assert.equal(isPageMounted(selecting, 50), false);
  assert.equal(isPageMounted(selecting, 62), true);
  assert.equal(isPageMounted(null, 1), false);
  // A region capture keeps the pages it started on while auto-scrolling.
  assert.deepEqual(mountedPageWindow({ start: 30, end: 30 }, 300, { overscan: 2, extend: { start: 20, end: 24 } }), { start: 20, end: 32, pinned: [] });
  // A distant saved page mounts only its neighbourhood, not every earlier page.
  const restored = mountedPageWindow({ start: 250, end: 250 }, 300);
  assert.ok(restored.end - restored.start + 1 <= 1 + 2 * PAGE_OVERSCAN);
  assert.equal(sameWindow(mountedPageWindow({ start: 5, end: 5 }, 9, { pinned: [1] }), { start: 3, end: 7, pinned: [1] }), true);
  assert.equal(sameWindow({ start: 1, end: 3, pinned: [] }, { start: 1, end: 4, pinned: [] }), false);
  assert.equal(sameWindow({ start: 1, end: 3, pinned: [9] }, { start: 1, end: 3, pinned: [] }), false);
  assert.equal(sameWindow(null, null), true);
});

test('raster resolution follows visible zoom, stays at base offscreen, and is pixel-bounded', () => {
  const base = { baseScale: 1.8, baseWidth: 760, aspect: 11 / 8.5, devicePixelRatio: 2 };
  const pixels = (scale, aspect = base.aspect) => (base.baseWidth * scale) ** 2 * aspect;
  // Matches the previous visible behaviour at and above the base zoom.
  assert.equal(rasterScale({ ...base, zoom: 1.8, visible: true }), 3.6);
  assert.equal(rasterScale({ ...base, zoom: 3, visible: true }), 4);
  assert.equal(rasterScale({ ...base, zoom: 32, visible: true }), 4);
  assert.equal(rasterScale({ ...base, zoom: 3, visible: false }), 3.6);
  // Below the base zoom the raster tracks the displayed device pixels instead of oversampling.
  assert.equal(rasterScale({ ...base, zoom: 1, visible: true }), 2);
  assert.equal(rasterScale({ ...base, zoom: 1, visible: false }), 2);
  assert.equal(rasterScale({ ...base, zoom: .1, visible: true }), 1);
  assert.equal(rasterScale({ ...base, zoom: 1.8, devicePixelRatio: 1, visible: true }), 1.8);
  assert.equal(rasterScale({ ...base, zoom: 2, devicePixelRatio: .5, visible: true }), 1);
  // Never above the per-canvas budget, including very tall pages at any DPR.
  for (const zoom of [.1, .5, 1, 1.8, 4, 12, 32]) {
    for (const aspect of [.5, 11 / 8.5, 3, 12]) {
      for (const devicePixelRatio of [1, 2, 3]) {
        for (const visible of [true, false]) assert.ok(pixels(rasterScale({ ...base, zoom, aspect, devicePixelRatio, visible }), aspect) <= MAX_RASTER_PIXELS * 1.0001);
      }
    }
  }
  assert.ok(rasterScale({ ...base, zoom: 4, aspect: 12, visible: true }) < 4);
});

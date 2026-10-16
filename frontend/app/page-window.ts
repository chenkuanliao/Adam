// Pure helpers for mounting and rasterizing only the PDF pages near the
// viewport. Unmounted pages keep placeholders with their final dimensions so
// scroll offsets, saved positions, and note anchors stay stable.

export type PageRange = { start: number; end: number };
// A contiguous window plus individually pinned pages, such as the endpoints of
// a live text selection. Pages between pinned endpoints may unmount: a DOM
// Range only needs its boundary nodes, and remounted text rejoins the range.
export type PageWindow = PageRange & { pinned: number[] };
export type VerticalRect = { top: number; bottom: number };

// Letter paper, used only until the first real page size is known.
export const DEFAULT_PAGE_ASPECT = 11 / 8.5;
// Keep a couple of pages mounted beyond the viewport so native selection can
// cross page boundaries and scrolling reveals already-rendered neighbours.
export const PAGE_OVERSCAN = 2;
// Roughly one 4096 × 4096 canvas: Safari's per-canvas limit and ~64 MB RGBA.
export const MAX_RASTER_PIXELS = 4096 * 4096;
// Rasters stop sharpening past 400%; CSS zoom scales the result beyond that.
export const MAX_RASTER_ZOOM = 4;
// Very small zoom levels keep a legible raster for region screenshots.
export const MIN_RASTER_ZOOM = .5;

/** Estimate unknown page aspects from the nearest earlier known page, then the first known page. */
export function pageAspects(known: ReadonlyArray<number | undefined>, count: number): number[] {
  const fallback = known.find((aspect) => aspect !== undefined && aspect > 0) ?? DEFAULT_PAGE_ASPECT;
  const result: number[] = [];
  let previous = fallback;
  for (let index = 0; index < count; index++) {
    const aspect = known[index];
    if (aspect !== undefined && aspect > 0) previous = aspect;
    result.push(aspect !== undefined && aspect > 0 ? aspect : previous);
  }
  return result;
}

/**
 * Find the 1-based pages intersecting [viewTop, viewBottom]. `rectOf` takes a
 * 0-based index; rects must be ordered from top to bottom. Uses binary search so
 * scroll handlers read only O(log n) page geometries.
 */
export function visiblePageRange(count: number, rectOf: (index: number) => VerticalRect, viewTop: number, viewBottom: number): PageRange | null {
  if (count <= 0) return null;
  // First page whose bottom is below the top of the view.
  let low = 0;
  let high = count - 1;
  let first = count - 1;
  while (low <= high) {
    const middle = (low + high) >> 1;
    if (rectOf(middle).bottom > viewTop) { first = middle; high = middle - 1; } else low = middle + 1;
  }
  // Last page whose top is above the bottom of the view.
  low = first;
  high = count - 1;
  let last = first;
  while (low <= high) {
    const middle = (low + high) >> 1;
    if (rectOf(middle).top < viewBottom) { last = middle; low = middle + 1; } else high = middle - 1;
  }
  return { start: first + 1, end: Math.max(first, last) + 1 };
}

/**
 * Expand a visible range by `overscan` pages. `extend` widens the contiguous
 * range (for example to keep pages under a region capture); `pinned` pages stay
 * mounted individually.
 */
export function mountedPageWindow(visible: PageRange | null, count: number, { overscan = PAGE_OVERSCAN, extend = null, pinned = [] }: { overscan?: number; extend?: PageRange | null; pinned?: number[] } = {}): PageWindow | null {
  if (count <= 0) return null;
  const base = visible ?? { start: 1, end: 1 };
  let start = Math.max(1, base.start - overscan);
  let end = Math.min(count, base.end + overscan);
  if (extend) {
    start = Math.max(1, Math.min(start, extend.start));
    end = Math.min(count, Math.max(end, extend.end));
  }
  const outside = [...new Set(pinned)].filter((page) => Number.isInteger(page) && page >= 1 && page <= count && (page < start || page > end)).sort((a, b) => a - b);
  return { start, end, pinned: outside };
}

export function isPageMounted(window: PageWindow | null, page: number) {
  return window !== null && ((page >= window.start && page <= window.end) || window.pinned.includes(page));
}

export function sameWindow(a: PageWindow | null, b: PageWindow | null) {
  return a === b || (a !== null && b !== null && a.start === b.start && a.end === b.end && a.pinned.length === b.pinned.length && a.pinned.every((page, index) => page === b.pinned[index]));
}

/**
 * Device pixels per PDF-layout unit for a page raster. Layout pages are
 * `baseWidth` CSS pixels wide and displayed with CSS zoom `zoom / baseScale`.
 * Visible pages follow the zoom (up to MAX_RASTER_ZOOM) at the device pixel
 * ratio; nearby offscreen pages never exceed the base scale. Every raster is
 * capped at MAX_RASTER_PIXELS.
 */
export function rasterScale({ zoom, baseScale, baseWidth, aspect, devicePixelRatio, visible }: { zoom: number; baseScale: number; baseWidth: number; aspect: number; devicePixelRatio: number; visible: boolean }) {
  const renderScale = Math.max(MIN_RASTER_ZOOM, Math.min(zoom, visible ? MAX_RASTER_ZOOM : baseScale));
  const pixelRatio = Math.min(devicePixelRatio || 1, MAX_RASTER_ZOOM / renderScale);
  const width = baseWidth * renderScale * pixelRatio;
  const pixels = width * width * aspect;
  const budget = pixels > MAX_RASTER_PIXELS ? Math.sqrt(MAX_RASTER_PIXELS / pixels) : 1;
  return renderScale * pixelRatio * budget;
}

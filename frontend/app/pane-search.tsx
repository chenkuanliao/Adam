'use client';
import { useEffect, useLayoutEffect, useMemo, useRef, useState, type RefObject } from 'react';
import type { PDFDocumentProxy } from 'pdfjs-dist/types/src/display/api';
import { findOffsets, textRanges } from './find-text';
import { createSearchHighlights, type HighlightRegistry, type SearchHighlight } from './search-highlights';

type Hit = { page: number; index: number };
const EMPTY_HITS: Hit[] = [];

export default function PaneSearch({ kind, root, pdf, onPage, onClose, initialQuery = '' }: { kind: 'pdf' | 'chat'; root: RefObject<HTMLElement | null>; pdf?: PDFDocumentProxy | null; onPage?: (page: number) => void; onClose: () => void; initialQuery?: string }) {
  const [query, setQuery] = useState(initialQuery);
  const [texts, setTexts] = useState<string[]>([]);
  const [loading, setLoading] = useState(kind === 'pdf');
  const [error, setError] = useState('');
  const [chatResult, setChatResult] = useState<{ query: string; hits: Hit[] }>({ query: '', hits: [] });
  const pdfHits = useMemo(() => kind === 'pdf' ? texts.flatMap((text, index) => findOffsets(text, query).map((_, hit) => ({ page: index + 1, index: hit }))) : [], [kind, texts, query]);
  const hits = kind === 'pdf' ? pdfHits : chatResult.query === query ? chatResult.hits : EMPTY_HITS;
  const highlights = useRef<ReturnType<typeof createSearchHighlights> | null>(null);
  const [current, setCurrent] = useState(0);
  const [revision, setRevision] = useState(0);
  const onPageRef = useRef(onPage);
  useEffect(() => { onPageRef.current = onPage; });
  const lastJump = useRef('');
  const lastScroll = useRef('');
  const label = kind === 'pdf' ? 'PDF' : 'chat';

  useEffect(() => {
    if (kind !== 'pdf' || !pdf) return;
    let cancelled = false;
    void (async () => {
      try {
        const pages: string[] = [];
        // PDF.js text is cached while the find bar is open. No page rasterization.
        for (let page = 1; page <= pdf.numPages; page++) {
          const content = await (await pdf.getPage(page)).getTextContent();
          if (cancelled) return;
          pages.push(content.items.map((item) => 'str' in item ? item.str + (item.hasEOL ? ' ' : '') : '').join(''));
        }
        setTexts(pages); setLoading(false);
      } catch { if (!cancelled) { setError('Could not read PDF text. Close and try again.'); setLoading(false); } }
    })();
    return () => { cancelled = true; };
  }, [kind, pdf]);

  useLayoutEffect(() => {
    const registry = (CSS as unknown as { highlights?: HighlightRegistry }).highlights;
    const HighlightClass = (window as unknown as { Highlight?: new () => SearchHighlight }).Highlight;
    if (!registry || !HighlightClass) return;
    const painter = createSearchHighlights(registry, () => new HighlightClass(), kind);
    highlights.current = painter;
    return () => { painter.dispose(); highlights.current = null; };
  }, [kind]);

  useEffect(() => {
    if (!root.current) return;
    let frame = 0;
    const observer = new MutationObserver(() => {
      // Streaming text and PDF text layers can arrive in many DOM mutations.
      // Refresh mounted ranges at most once per animation frame.
      if (!frame) frame = requestAnimationFrame(() => { frame = 0; setRevision((value) => value + 1); });
    });
    observer.observe(root.current, { childList: true, subtree: true, characterData: true });
    return () => { observer.disconnect(); cancelAnimationFrame(frame); };
  }, [root]);

  useEffect(() => {
    if (kind !== 'chat') return;
    const frame = requestAnimationFrame(() => {
      const results = root.current ? textRanges(root.current, query, '.user-message p, .markdown-answer').map((_, index) => ({ page: 0, index })) : [];
      setChatResult({ query, hits: results });
      setCurrent((value) => Math.min(value, Math.max(0, results.length - 1)));
    });
    return () => cancelAnimationFrame(frame);
  }, [query, kind, root, revision]);

  useLayoutEffect(() => {
    const hit = hits[current];
    const jumpKey = `${query}:${current}:${hit?.page}`;
    if (kind === 'pdf' && hit && jumpKey !== lastJump.current) { onPageRef.current?.(hit.page); lastJump.current = jumpKey; }
    const element = root.current;
    if (!element) return;
    const ranges = textRanges(element, query, kind === 'pdf' ? '.react-pdf__Page__textContent' : '.user-message p, .markdown-answer');
    const page = hit && kind === 'pdf' ? element.querySelector(`[data-page-number="${hit.page}"]`) : null;
    const selected = kind === 'pdf' ? (page ? textRanges(page, query, '.react-pdf__Page__textContent')[hit.index] : undefined) : ranges[current];
    highlights.current?.replace(ranges, selected);
    if (selected && jumpKey !== lastScroll.current) {
      const rect = selected.getBoundingClientRect();
      const bounds = element.getBoundingClientRect();
      if (rect.height && (rect.top < bounds.top + 70 || rect.bottom > bounds.bottom - 30)) element.scrollBy({ top: rect.top - bounds.top - bounds.height / 3 });
      if (rect.height) lastScroll.current = jumpKey;
    }
  }, [query, current, hits, root, kind, revision]);

  function move(delta: number) { setCurrent((value) => hits.length ? (value + delta + hits.length) % hits.length : 0); }
  return <div className={`pane-find ${kind}-find`} role="search" aria-label={`Find in ${label}`} onKeyDown={(event) => { if (event.key === 'Escape') { event.preventDefault(); event.stopPropagation(); onClose(); } if (event.key === 'Enter') { event.preventDefault(); move(event.shiftKey ? -1 : 1); } }}>
    <span className="find-symbol" aria-hidden="true">⌕</span><input autoFocus aria-label={`Find in ${label}`} placeholder={`Find in ${label}…`} value={query} onChange={(event) => { highlights.current?.clear(); setQuery(event.target.value); setCurrent(0); }} />
    <span className="find-count" role="status">{loading ? 'Reading…' : error ? 'Error' : query.trim() ? hits.length ? `${current + 1} / ${hits.length}` : 'No matches' : label}</span>
    <button type="button" aria-label={`Previous ${label} match`} disabled={!hits.length} onClick={() => move(-1)}>↑</button><button type="button" aria-label={`Next ${label} match`} disabled={!hits.length} onClick={() => move(1)}>↓</button><button type="button" aria-label={`Close ${label} search`} onClick={onClose}>×</button>
    {error && <small role="alert">{error}</small>}
  </div>;
}

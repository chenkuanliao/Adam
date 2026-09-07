'use client';
/* eslint-disable @next/next/no-img-element -- context thumbnails are local data URLs */

import { FormEvent, useCallback, useEffect, useLayoutEffect, useRef, useState, type CSSProperties } from 'react';
import { Document as PdfDocument, Page, pdfjs } from 'react-pdf';
import type { PDFPageProxy, TextContent } from 'pdfjs-dist/types/src/display/api';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import remarkMath from 'remark-math';
import rehypeKatex from 'rehype-katex';
import 'katex/dist/katex.min.css';
import 'react-pdf/dist/Page/AnnotationLayer.css';
import 'react-pdf/dist/Page/TextLayer.css';

// Keep the PDF worker static. Importing it through Vite's dev module graph
// injects the HMR browser client into the worker, where `window` is unavailable.
pdfjs.GlobalWorkerOptions.workerSrc = '/pdf.worker.min.mjs';

type Paper = { id: string; original_name: string; page_count: number; status: string; created_at: string };
type ContextSelection = { id: string; text: string; page: number | null; pageEnd?: number | null; imageDataUrl?: string };
type PendingSelection = ContextSelection & { x: number; y: number; range: Range };
type ScreenshotDrag = { startContentX: number; startContentY: number; currentContentX: number; currentContentY: number; overlayLeft: number; overlayTop: number; overlayWidth: number; overlayHeight: number };
type HighlightRect = { left: number; top: number; width: number; height: number };
type HighlightEntry = ContextSelection & { color: string; range: Range | null; rects: HighlightRect[] };
type StreamStatus = 'idle' | 'connecting' | 'streaming' | 'complete' | 'error';
type ChatTurn = { id: string; question: string; answer: string; context: ContextSelection[] };
const API_BASE = '';
const MIN_ZOOM = .6;
const MAX_ZOOM = 1.8;
const DEFAULT_ZOOM = MAX_ZOOM;
const MIN_CHAT_SCALE = .85;
const MAX_CHAT_SCALE = 1.35;
const CHAT_SCALE_STEP = .1;
const DEFAULT_CHAT_SCALE = 1.2;
const BASE_PAGE_WIDTH = 760;
const HIGHLIGHT_COLORS = [
  { color: '#f8e58c', label: 'Yellow', key: '1' },
  { color: '#bfe6cd', label: 'Green', key: '2' },
  { color: '#bcdcf4', label: 'Blue', key: '3' },
  { color: '#e6c8ed', label: 'Purple', key: '4' },
] as const;

export default function Home() {
  const [papers, setPapers] = useState<Paper[]>([]);
  const [active, setActive] = useState<Paper | null>(null);
  const [pages, setPages] = useState(0);
  const [contextSelections, setContextSelections] = useState<ContextSelection[]>([]);
  const [pendingSelection, setPendingSelection] = useState<PendingSelection | null>(null);
  const [screenshotMode, setScreenshotMode] = useState(false);
  const [screenshotDrag, setScreenshotDrag] = useState<ScreenshotDrag | null>(null);
  const [highlightEntries, setHighlightEntries] = useState<HighlightEntry[]>([]);
  const [question, setQuestion] = useState('');
  const [submittedQuestion, setSubmittedQuestion] = useState('');
  const [submittedContext, setSubmittedContext] = useState<ContextSelection[]>([]);
  const [answer, setAnswer] = useState('');
  const [chatHistory, setChatHistory] = useState<ChatTurn[]>([]);
  const [streamStatus, setStreamStatus] = useState<StreamStatus>('idle');
  const [zoom, setZoom] = useState(DEFAULT_ZOOM);
  const [chatScale, setChatScale] = useState(DEFAULT_CHAT_SCALE);
  const [uploading, setUploading] = useState(false);
  const [asking, setAsking] = useState(false);
  const [error, setError] = useState('');
  const viewerRef = useRef<HTMLDivElement>(null);
  const chatBodyRef = useRef<HTMLDivElement>(null);
  const composerRef = useRef<HTMLTextAreaElement>(null);
  const focusComposerAfterContextRef = useRef(false);
  const followOutputRef = useRef(true);
  const autoScrollFrameRef = useRef<number | null>(null);
  const screenshotScrollFrameRef = useRef<number | null>(null);
  const screenshotPointerRef = useRef<{ x: number; y: number } | null>(null);
  const pendingZoomRef = useRef<{ page: number; y: number } | null>(null);
  const answerQueueRef = useRef('');
  const revealTimerRef = useRef<number | null>(null);
  const streamFinishedRef = useRef(false);
  const zoomRef = useRef(DEFAULT_ZOOM);
  const highlightIdsRef = useRef<string[]>([]);
  const highlightEntriesRef = useRef<HighlightEntry[]>([]);
  const highlightUndoRef = useRef<HighlightEntry[][]>([]);
  const highlightRedoRef = useRef<HighlightEntry[][]>([]);
  const isScreenshotDragging = screenshotDrag !== null;

  const loadPapers = useCallback(async () => {
    try {
      const response = await fetch(`${API_BASE}/api/documents`);
      if (!response.ok) throw new Error('Could not load your paper library.');
      setPapers(await response.json());
      setError('');
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'The local service is unavailable.');
    }
  }, []);

  const changeZoom = useCallback((delta: number) => {
    const currentZoom = zoomRef.current;
    const nextZoom = Math.min(MAX_ZOOM, Math.max(MIN_ZOOM, Number((currentZoom + delta).toFixed(2))));
    if (nextZoom === currentZoom) return;

    const viewer = viewerRef.current;
    if (viewer) {
      const viewerRect = viewer.getBoundingClientRect();
      const centerY = viewerRect.top + viewer.clientHeight / 2;
      const pageElements = Array.from(viewer.querySelectorAll<HTMLElement>('[data-page-number]'));
      const focusedPage = pageElements.reduce<HTMLElement | null>((closest, page) => {
        const rect = page.getBoundingClientRect();
        const distance = centerY < rect.top ? rect.top - centerY : centerY > rect.bottom ? centerY - rect.bottom : 0;
        if (!closest) return page;
        const closestRect = closest.getBoundingClientRect();
        const closestDistance = centerY < closestRect.top ? closestRect.top - centerY : centerY > closestRect.bottom ? centerY - closestRect.bottom : 0;
        return distance < closestDistance ? page : closest;
      }, null);
      if (focusedPage) {
        const rect = focusedPage.getBoundingClientRect();
        pendingZoomRef.current = {
          page: Number(focusedPage.dataset.pageNumber),
          y: Math.min(1, Math.max(0, (centerY - rect.top) / rect.height)),
        };
      }
    }

    zoomRef.current = nextZoom;
    setZoom(nextZoom);
  }, []);

  const changeChatScale = useCallback((delta: number) => {
    setChatScale((current) => Math.min(MAX_CHAT_SCALE, Math.max(MIN_CHAT_SCALE, Number((current + delta).toFixed(2)))));
  }, []);

  useEffect(() => {
    // The initial library fetch intentionally synchronizes remote state after mount.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    void loadPapers();
  }, [loadPapers]);

  useLayoutEffect(() => {
    const focus = pendingZoomRef.current;
    if (!focus) return;
    const viewer = viewerRef.current;
    if (!viewer) return;
    const page = viewer.querySelector<HTMLElement>(`[data-page-number="${focus.page}"]`);
    if (!page) return;
    const viewerRect = viewer.getBoundingClientRect();
    const pageRect = page.getBoundingClientRect();
    viewer.scrollTop += pageRect.top + focus.y * pageRect.height - (viewerRect.top + viewer.clientHeight / 2);
    viewer.scrollLeft = 0;
    pendingZoomRef.current = null;
  }, [zoom]);

  useLayoutEffect(() => {
    if (!focusComposerAfterContextRef.current || contextSelections.length === 0) return;
    focusComposerAfterContextRef.current = false;
    composerRef.current?.focus({ preventScroll: true });
  }, [contextSelections.length]);

  useEffect(() => {
    const viewer = viewerRef.current;
    if (!viewer || !active) return;

    const handleViewerWheel = (event: WheelEvent) => {
      // Browsers expose trackpad pinch as a wheel event with ctrlKey set.
      // Also support explicit Ctrl/Command + mouse-wheel zoom.
      if (!event.ctrlKey && !event.metaKey) {
        if (event.shiftKey || Math.abs(event.deltaX) > Math.abs(event.deltaY)) {
          event.preventDefault();
          viewer.scrollLeft = 0;
        }
        return;
      }
      event.preventDefault();
      const direction = event.deltaY < 0 ? 1 : -1;
      const magnitude = Math.min(.12, Math.max(.02, Math.abs(event.deltaY) / 500));
      changeZoom(direction * magnitude);
    };
    const lockHorizontalPosition = () => {
      if (viewer.scrollLeft !== 0) viewer.scrollLeft = 0;
    };

    viewer.addEventListener('wheel', handleViewerWheel, { passive: false });
    viewer.addEventListener('scroll', lockHorizontalPosition, { passive: true });
    return () => {
      viewer.removeEventListener('wheel', handleViewerWheel);
      viewer.removeEventListener('scroll', lockHorizontalPosition);
    };
  }, [active, changeZoom]);

  useEffect(() => () => {
    if (revealTimerRef.current !== null) window.clearInterval(revealTimerRef.current);
    if (autoScrollFrameRef.current !== null) window.cancelAnimationFrame(autoScrollFrameRef.current);
    if (screenshotScrollFrameRef.current !== null) window.cancelAnimationFrame(screenshotScrollFrameRef.current);
    const highlights = (CSS as typeof CSS & { highlights?: Map<string, Highlight> }).highlights;
    highlightIdsRef.current.forEach((id) => highlights?.delete(id));
    document.querySelectorAll('style[data-highlight-id^="adam-"]').forEach((style) => style.remove());
    document.querySelectorAll('.react-pdf__Page > .pdf-highlight-mark').forEach((mark) => mark.remove());
  }, []);

  useEffect(() => {
    if (!isScreenshotDragging) return;
    const viewer = viewerRef.current;
    if (!viewer) return;
    const edgeSize = 72;
    const maxSpeed = 18;
    const tick = () => {
      const pointer = screenshotPointerRef.current;
      if (!pointer) return;
      const viewerRect = viewer.getBoundingClientRect();
      let speed = 0;
      if (pointer.y < viewerRect.top + edgeSize) speed = -maxSpeed * Math.min(1, (viewerRect.top + edgeSize - pointer.y) / edgeSize);
      else if (pointer.y > viewerRect.bottom - edgeSize) speed = maxSpeed * Math.min(1, (pointer.y - (viewerRect.bottom - edgeSize)) / edgeSize);
      if (speed !== 0) {
        const previousScrollTop = viewer.scrollTop;
        viewer.scrollTop += speed;
        if (viewer.scrollTop !== previousScrollTop) {
          setScreenshotDrag((drag) => {
            if (!drag) return null;
            const startX = viewerRect.left + drag.startContentX - viewer.scrollLeft;
            const startY = viewerRect.top + drag.startContentY - viewer.scrollTop;
            return { ...drag, currentContentX: pointer.x - viewerRect.left + viewer.scrollLeft, currentContentY: pointer.y - viewerRect.top + viewer.scrollTop, overlayLeft: Math.min(startX, pointer.x), overlayTop: Math.min(startY, pointer.y), overlayWidth: Math.abs(pointer.x - startX), overlayHeight: Math.abs(pointer.y - startY) };
          });
        }
      }
      screenshotScrollFrameRef.current = window.requestAnimationFrame(tick);
    };
    screenshotScrollFrameRef.current = window.requestAnimationFrame(tick);
    return () => {
      if (screenshotScrollFrameRef.current !== null) window.cancelAnimationFrame(screenshotScrollFrameRef.current);
      screenshotScrollFrameRef.current = null;
    };
  }, [isScreenshotDragging]);

  useEffect(() => {
    const chatBody = chatBodyRef.current;
    if (!chatBody || !followOutputRef.current) return;
    if (autoScrollFrameRef.current !== null) window.cancelAnimationFrame(autoScrollFrameRef.current);
    autoScrollFrameRef.current = window.requestAnimationFrame(() => {
      if (followOutputRef.current) chatBody.scrollTop = chatBody.scrollHeight;
      autoScrollFrameRef.current = null;
    });
    return () => {
      if (autoScrollFrameRef.current !== null) window.cancelAnimationFrame(autoScrollFrameRef.current);
      autoScrollFrameRef.current = null;
    };
  }, [answer, submittedQuestion, streamStatus, chatHistory.length, contextSelections.length]);

  const revealNewContext = useCallback(() => {
    followOutputRef.current = true;
    focusComposerAfterContextRef.current = true;
  }, []);

  function pauseChatFollow() {
    followOutputRef.current = false;
    if (autoScrollFrameRef.current !== null) window.cancelAnimationFrame(autoScrollFrameRef.current);
    autoScrollFrameRef.current = null;
  }

  function startAnswerReveal() {
    if (revealTimerRef.current !== null) return;
    revealTimerRef.current = window.setInterval(() => {
      const queued = answerQueueRef.current;
      if (queued) {
        // Provider events can contain a full sentence. Reveal a small adaptive
        // slice so the UI still reads as a live stream without falling behind.
        const amount = Math.min(10, Math.max(1, Math.ceil(queued.length / 30)));
        setAnswer((current) => current + queued.slice(0, amount));
        answerQueueRef.current = queued.slice(amount);
        setStreamStatus('streaming');
      } else if (streamFinishedRef.current) {
        if (revealTimerRef.current !== null) window.clearInterval(revealTimerRef.current);
        revealTimerRef.current = null;
        setStreamStatus('complete');
        setAsking(false);
      }
    }, 40);
  }

  async function upload(file: File) {
    if (file.type !== 'application/pdf' && !file.name.toLowerCase().endsWith('.pdf')) {
      setError('Choose a PDF file.');
      return;
    }
    setUploading(true);
    setError('');
    const body = new FormData();
    body.append('file', file);
    try {
      const response = await fetch(`${API_BASE}/api/documents`, { method: 'POST', body });
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.detail ?? 'Upload failed.');
      await loadPapers();
      openPaper(payload);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Upload failed.');
    } finally {
      setUploading(false);
    }
  }

  function openPaper(paper: Paper) {
    setActive(paper);
    setPages(0);
    setContextSelections([]);
    setPendingSelection(null);
    clearHighlights();
    setQuestion('');
    setSubmittedQuestion('');
    setSubmittedContext([]);
    setAnswer('');
    setChatHistory([]);
    setStreamStatus('idle');
    zoomRef.current = DEFAULT_ZOOM;
    setZoom(DEFAULT_ZOOM);
    setError('');
    void loadAnnotations(paper.id);
  }

  async function loadAnnotations(documentId: string) {
    try {
      const response = await fetch(`${API_BASE}/api/documents/${documentId}/annotations`);
      if (!response.ok) throw new Error('Could not load saved highlights.');
      const annotations = await response.json() as Array<{ id: string; page: number; text: string; color: string; rects: HighlightRect[] }>;
      const entries = annotations.map((item) => ({ ...item, range: null }));
      highlightEntriesRef.current = entries;
      highlightUndoRef.current = [];
      highlightRedoRef.current = [];
      paintHighlights(entries);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Could not load saved highlights.');
    }
  }

  function captureSelection(event: React.MouseEvent<HTMLDivElement>) {
    const domSelection = window.getSelection();
    const text = domSelection?.toString().trim();
    if (!text || !viewerRef.current?.contains(domSelection?.anchorNode ?? null)) {
      setPendingSelection(null);
      return;
    }
    const range = domSelection!.getRangeAt(0).cloneRange();
    const rect = range.getBoundingClientRect();
    const pageElement = (event.target as HTMLElement).closest<HTMLElement>('[data-page-number]');
    const page = pageElement ? Number(pageElement.dataset.pageNumber) : null;
    const existing = highlightEntriesRef.current.find((item) => item.page === page && (item.text === text || (item.range && item.range.startContainer === range.startContainer && item.range.startOffset === range.startOffset && item.range.endContainer === range.endContainer && item.range.endOffset === range.endOffset)));
    if (existing) {
      commitHighlights(highlightEntriesRef.current.filter((item) => item.id !== existing.id));
      clearBrowserSelection();
      return;
    }
    setPendingSelection({ id: crypto.randomUUID(), text, page, range, x: Math.min(window.innerWidth - 24, Math.max(24, rect.left + rect.width / 2)), y: Math.max(12, rect.top - 12) });
  }

  function clearBrowserSelection() {
    window.getSelection()?.removeAllRanges();
    setPendingSelection(null);
  }

  function addSelectionToContext() {
    if (!pendingSelection) return;
    const { id, text, page } = pendingSelection;
    const alreadyAdded = contextSelections.some((item) => item.text === text && item.page === page);
    if (!alreadyAdded) {
      revealNewContext();
      setContextSelections((current) => [...current, { id, text, page }]);
    }
    clearBrowserSelection();
    if (alreadyAdded) window.requestAnimationFrame(() => composerRef.current?.focus({ preventScroll: true }));
  }

  useEffect(() => {
    const handleScreenshotHotkey = (event: KeyboardEvent) => {
      const target = event.target as HTMLElement | null;
      if (event.repeat || event.metaKey || event.ctrlKey || event.altKey || target?.isContentEditable || target?.matches('input, textarea, select')) return;
      if (event.key.toLowerCase() === 'r') {
        event.preventDefault();
        clearBrowserSelection();
        setScreenshotDrag(null);
        screenshotPointerRef.current = null;
        setScreenshotMode(true);
      } else if (event.key === 'Escape' && screenshotMode) {
        event.preventDefault();
        setScreenshotDrag(null);
        screenshotPointerRef.current = null;
        setScreenshotMode(false);
      }
    };
    window.addEventListener('keydown', handleScreenshotHotkey);
    return () => window.removeEventListener('keydown', handleScreenshotHotkey);
  });

  function beginScreenshot(event: React.PointerEvent<HTMLElement>) {
    if (!screenshotMode || event.button !== 0) return;
    const pageElement = document.elementFromPoint(event.clientX, event.clientY)?.closest<HTMLElement>('.react-pdf__Page');
    if (!pageElement || !viewerRef.current) return;
    event.preventDefault();
    event.currentTarget.setPointerCapture(event.pointerId);
    const viewerRect = viewerRef.current.getBoundingClientRect();
    const contentX = event.clientX - viewerRect.left + viewerRef.current.scrollLeft;
    const contentY = event.clientY - viewerRect.top + viewerRef.current.scrollTop;
    screenshotPointerRef.current = { x: event.clientX, y: event.clientY };
    setScreenshotDrag({ startContentX: contentX, startContentY: contentY, currentContentX: contentX, currentContentY: contentY, overlayLeft: event.clientX, overlayTop: event.clientY, overlayWidth: 0, overlayHeight: 0 });
  }

  function moveScreenshot(event: React.PointerEvent<HTMLElement>) {
    if (!screenshotDrag) return;
    event.preventDefault();
    screenshotPointerRef.current = { x: event.clientX, y: event.clientY };
    setScreenshotDrag((drag) => {
      const viewer = viewerRef.current;
      if (!drag || !viewer) return drag;
      const viewerRect = viewer.getBoundingClientRect();
      const startX = viewerRect.left + drag.startContentX - viewer.scrollLeft;
      const startY = viewerRect.top + drag.startContentY - viewer.scrollTop;
      return { ...drag, currentContentX: event.clientX - viewerRect.left + viewer.scrollLeft, currentContentY: event.clientY - viewerRect.top + viewer.scrollTop, overlayLeft: Math.min(startX, event.clientX), overlayTop: Math.min(startY, event.clientY), overlayWidth: Math.abs(event.clientX - startX), overlayHeight: Math.abs(event.clientY - startY) };
    });
  }

  function finishScreenshot(event: React.PointerEvent<HTMLElement>) {
    if (!screenshotDrag) return;
    event.preventDefault();
    const viewer = viewerRef.current;
    if (!viewer) return;
    const viewerRect = viewer.getBoundingClientRect();
    const currentContentX = event.clientX - viewerRect.left + viewer.scrollLeft;
    const currentContentY = event.clientY - viewerRect.top + viewer.scrollTop;
    const selectionLeft = Math.min(screenshotDrag.startContentX, currentContentX);
    const selectionTop = Math.min(screenshotDrag.startContentY, currentContentY);
    const selectionRight = Math.max(screenshotDrag.startContentX, currentContentX);
    const selectionBottom = Math.max(screenshotDrag.startContentY, currentContentY);
    setScreenshotDrag(null);
    screenshotPointerRef.current = null;
    setScreenshotMode(false);
    const selectionWidth = selectionRight - selectionLeft;
    const selectionHeight = selectionBottom - selectionTop;
    if (selectionWidth < 6 || selectionHeight < 6) return;
    const intersectingPages = Array.from(viewer.querySelectorAll<HTMLElement>('.react-pdf__Page')).map((element) => {
      const rect = element.getBoundingClientRect();
      const left = rect.left - viewerRect.left + viewer.scrollLeft;
      const top = rect.top - viewerRect.top + viewer.scrollTop;
      return { element, rect, left, top, right: left + rect.width, bottom: top + rect.height, page: Number(element.closest<HTMLElement>('[data-page-number]')?.dataset.pageNumber) };
    }).filter((item) => item.right > selectionLeft && item.left < selectionRight && item.bottom > selectionTop && item.top < selectionBottom);
    if (!intersectingPages.length) return;
    const firstCanvas = intersectingPages[0].element.querySelector<HTMLCanvasElement>('.react-pdf__Page__canvas');
    if (!firstCanvas) return;
    const nativeScale = firstCanvas.width / intersectingPages[0].rect.width;
    const outputScale = Math.min(nativeScale, 4096 / Math.max(selectionWidth, selectionHeight));
    const output = document.createElement('canvas');
    output.width = Math.max(1, Math.round(selectionWidth * outputScale));
    output.height = Math.max(1, Math.round(selectionHeight * outputScale));
    const outputContext = output.getContext('2d');
    if (!outputContext) return;
    outputContext.fillStyle = '#fff';
    outputContext.fillRect(0, 0, output.width, output.height);
    intersectingPages.forEach(({ element, rect, left, top, right, bottom }) => {
      const source = element.querySelector<HTMLCanvasElement>('.react-pdf__Page__canvas');
      if (!source) return;
      const cropLeft = Math.max(selectionLeft, left);
      const cropTop = Math.max(selectionTop, top);
      const cropRight = Math.min(selectionRight, right);
      const cropBottom = Math.min(selectionBottom, bottom);
      const sourceScaleX = source.width / rect.width;
      const sourceScaleY = source.height / rect.height;
      outputContext.drawImage(source, (cropLeft - left) * sourceScaleX, (cropTop - top) * sourceScaleY, (cropRight - cropLeft) * sourceScaleX, (cropBottom - cropTop) * sourceScaleY, (cropLeft - selectionLeft) * outputScale, (cropTop - selectionTop) * outputScale, (cropRight - cropLeft) * outputScale, (cropBottom - cropTop) * outputScale);
    });
    const page = Math.min(...intersectingPages.map((item) => item.page));
    const pageEnd = Math.max(...intersectingPages.map((item) => item.page));
    revealNewContext();
    setContextSelections((current) => [...current, { id: crypto.randomUUID(), text: 'Selected PDF area', page, pageEnd, imageDataUrl: output.toDataURL('image/jpeg', .92) }]);
  }

  function applyHighlight(color: string) {
    if (!pendingSelection) return;
    const { id, text, page, range } = pendingSelection;
    const pageElement = (range.startContainer.nodeType === Node.ELEMENT_NODE ? range.startContainer as Element : range.startContainer.parentElement)?.closest<HTMLElement>('.react-pdf__Page');
    if (!pageElement) return;
    const rects = getHighlightRects(range, pageElement);
    commitHighlights([...highlightEntriesRef.current, { id, text, page, range, color, rects }]);
    clearBrowserSelection();
  }

  function getHighlightRects(range: Range, page: HTMLElement): HighlightRect[] {
    const pageRect = page.getBoundingClientRect();
    const fragments = Array.from(range.getClientRects()).filter((rect) => rect.width > 1 && rect.height > 1).map((rect) => ({ left: rect.left, top: rect.top, right: rect.right, bottom: rect.bottom })).sort((a, b) => a.top - b.top || a.left - b.left);
    const lines: typeof fragments = [];
    fragments.forEach((fragment) => {
      const line = lines.find((candidate) => {
        const overlap = Math.min(candidate.bottom, fragment.bottom) - Math.max(candidate.top, fragment.top);
        return overlap > Math.min(candidate.bottom - candidate.top, fragment.bottom - fragment.top) * .55;
      });
      if (line) {
        line.left = Math.min(line.left, fragment.left);
        line.top = Math.min(line.top, fragment.top);
        line.right = Math.max(line.right, fragment.right);
        line.bottom = Math.max(line.bottom, fragment.bottom);
      } else lines.push(fragment);
    });
    lines.sort((a, b) => a.top - b.top);
    for (let index = 0; index < lines.length - 1; index += 1) {
      if (lines[index].bottom > lines[index + 1].top) {
        const boundary = (lines[index].bottom + lines[index + 1].top) / 2;
        lines[index].bottom = boundary;
        lines[index + 1].top = boundary;
      }
    }
    return lines.map((rect) => ({ left: (rect.left - pageRect.left) / pageRect.width, top: (rect.top - pageRect.top) / pageRect.height, width: (rect.right - rect.left) / pageRect.width, height: (rect.bottom - rect.top) / pageRect.height }));
  }

  function paintHighlights(entries: HighlightEntry[]) {
    // Remove marks from both the current rectangle renderer and the previous
    // CSS Highlight implementation (the latter matters during hot reload).
    const highlights = (CSS as typeof CSS & { highlights?: Map<string, Highlight> }).highlights;
    highlightIdsRef.current.forEach((id) => highlights?.delete(id));
    document.querySelectorAll('style[data-highlight-id^="adam-"]').forEach((style) => style.remove());
    document.querySelectorAll('.react-pdf__Page > .pdf-highlight-mark').forEach((mark) => mark.remove());
    highlightIdsRef.current = [];
    setHighlightEntries(entries);
  }

  function commitHighlights(next: HighlightEntry[]) {
    const previous = highlightEntriesRef.current;
    highlightUndoRef.current.push(highlightEntriesRef.current);
    highlightRedoRef.current = [];
    highlightEntriesRef.current = next;
    paintHighlights(next);
    void persistHighlightChanges(previous, next);
  }

  async function persistHighlightChanges(previous: HighlightEntry[], next: HighlightEntry[]) {
    if (!active) return;
    const removed = previous.filter((item) => !next.some((candidate) => candidate.id === item.id));
    const added = next.filter((item) => !previous.some((candidate) => candidate.id === item.id) && item.page);
    try {
      await Promise.all([
        ...removed.map((item) => fetch(`${API_BASE}/api/documents/${active.id}/annotations/${item.id}`, { method: 'DELETE' }).then((response) => { if (!response.ok && response.status !== 404) throw new Error(); })),
        ...added.map((item) => fetch(`${API_BASE}/api/documents/${active.id}/annotations`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ id: item.id, page: item.page, text: item.text, color: item.color, rects: item.rects }) }).then((response) => { if (!response.ok) throw new Error(); })),
      ]);
    } catch {
      setError('A highlight could not be saved. Please try again.');
    }
  }

  function undoHighlight() {
    const previous = highlightUndoRef.current.pop();
    if (!previous) return;
    highlightRedoRef.current.push(highlightEntriesRef.current);
    highlightEntriesRef.current = previous;
    paintHighlights(previous);
    void persistHighlightChanges(highlightRedoRef.current.at(-1) ?? [], previous);
    clearBrowserSelection();
  }

  function redoHighlight() {
    const next = highlightRedoRef.current.pop();
    if (!next) return;
    highlightUndoRef.current.push(highlightEntriesRef.current);
    highlightEntriesRef.current = next;
    paintHighlights(next);
    void persistHighlightChanges(highlightUndoRef.current.at(-1) ?? [], next);
    clearBrowserSelection();
  }

  function clearHighlights() {
    const highlights = (CSS as typeof CSS & { highlights?: Map<string, Highlight> }).highlights;
    highlightIdsRef.current.forEach((id) => highlights?.delete(id));
    document.querySelectorAll('style[data-highlight-id^="adam-"]').forEach((style) => style.remove());
    document.querySelectorAll('.react-pdf__Page > .pdf-highlight-mark').forEach((mark) => mark.remove());
    highlightIdsRef.current = [];
    highlightEntriesRef.current = [];
    highlightUndoRef.current = [];
    highlightRedoRef.current = [];
    setHighlightEntries([]);
  }

  useEffect(() => {
    const handleHistoryHotkey = (event: KeyboardEvent) => {
      const target = event.target as HTMLElement | null;
      if (!(event.metaKey || event.ctrlKey) || event.altKey || event.key.toLowerCase() !== 'z' || target?.isContentEditable || target?.matches('input, textarea, select')) return;
      event.preventDefault();
      if (event.shiftKey) redoHighlight(); else undoHighlight();
    };
    window.addEventListener('keydown', handleHistoryHotkey);
    return () => window.removeEventListener('keydown', handleHistoryHotkey);
  });

  useEffect(() => {
    if (!pendingSelection) return;
    const handleSelectionHotkey = (event: KeyboardEvent) => {
      const target = event.target as HTMLElement | null;
      if (event.repeat || event.metaKey || event.ctrlKey || event.altKey || target?.isContentEditable || target?.matches('input, textarea, select')) return;
      const highlight = HIGHLIGHT_COLORS.find((item) => item.key === event.key);
      if (highlight) {
        event.preventDefault();
        applyHighlight(highlight.color);
      } else if (event.key.toLowerCase() === 'c') {
        event.preventDefault();
        addSelectionToContext();
      } else if (event.key === 'Escape') {
        event.preventDefault();
        clearBrowserSelection();
      }
    };
    window.addEventListener('keydown', handleSelectionHotkey);
    return () => window.removeEventListener('keydown', handleSelectionHotkey);
  });

  async function ask(event: FormEvent) {
    event.preventDefault();
    const hasConversationContext = Boolean(submittedQuestion && answer);
    if (!active || (contextSelections.length === 0 && !hasConversationContext) || !question.trim() || asking) return;
    const sentQuestion = question.trim();
    const sentContext = contextSelections.map((selection) => ({ ...selection }));
    if (submittedQuestion && answer) {
      setChatHistory((current) => [...current, { id: crypto.randomUUID(), question: submittedQuestion, answer, context: submittedContext }]);
    }
    followOutputRef.current = true;
    setAsking(true);
    setQuestion('');
    setSubmittedQuestion(sentQuestion);
    setSubmittedContext(sentContext);
    setContextSelections([]);
    setAnswer('');
    setError('');
    setStreamStatus('connecting');
    answerQueueRef.current = '';
    streamFinishedRef.current = false;
    if (revealTimerRef.current !== null) window.clearInterval(revealTimerRef.current);
    revealTimerRef.current = null;
    startAnswerReveal();
    try {
      const response = await fetch(`${API_BASE}/api/chat/stream`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ document_id: active.id, question: sentQuestion, selected_text: formatContext(sentContext), images: formatImages(sentContext), page: sentContext.length === 1 ? sentContext[0].page : null, history: [...chatHistory, ...(submittedQuestion && answer ? [{ id: 'current', question: submittedQuestion, answer, context: submittedContext }] : [])].slice(-10).map(({ question: priorQuestion, answer: priorAnswer, context }) => ({ question: priorQuestion, answer: priorAnswer, selected_text: formatContext(context), images: formatImages(context), page: context.length === 1 ? context[0].page : null })) }),
      });
      if (!response.ok || !response.body) {
        const payload = await response.json().catch(() => ({}));
        throw new Error(payload.detail ?? 'The model request failed.');
      }
      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      let buffer = '';
      while (true) {
        const { value, done } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        const events = buffer.split('\n\n');
        buffer = events.pop() ?? '';
        for (const block of events) {
          const line = block.split('\n').find((item) => item.startsWith('data: '));
          if (!line) continue;
          const data = JSON.parse(line.slice(6));
          if (data.type === 'started') setStreamStatus('connecting');
          if (data.type === 'delta') answerQueueRef.current += data.text;
          if (data.type === 'completed') streamFinishedRef.current = true;
          if (data.type === 'error') throw new Error(data.message);
        }
      }
      streamFinishedRef.current = true;
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'The model request failed.');
      streamFinishedRef.current = false;
      setStreamStatus('error');
      setAsking(false);
      if (revealTimerRef.current !== null) window.clearInterval(revealTimerRef.current);
      revealTimerRef.current = null;
    }
  }

  const canAsk = contextSelections.length > 0 || Boolean(submittedQuestion && answer);

  if (!active) return (
    <main className="library-shell">
      <header className="library-header"><Brand /><UploadButton uploading={uploading} upload={upload} /></header>
      <section className="library-content">
        <p className="eyebrow">Your research desk</p>
        <h1>Read closely. Ask instantly.</h1>
        <p className="intro">Your papers and reading context stay on this machine. Select a passage and ask without breaking focus.</p>
        <label className="dropzone" onDragOver={(event) => event.preventDefault()} onDrop={(event) => { event.preventDefault(); const file = event.dataTransfer.files[0]; if (file) void upload(file); }}><span className="drop-icon">↥</span><strong>{uploading ? 'Processing your paper…' : 'Drop a research paper here'}</strong><span>or click to choose a PDF</span><input type="file" accept="application/pdf" hidden disabled={uploading} onChange={(event) => event.target.files?.[0] && void upload(event.target.files[0])} /></label>
        {error && <p className="error-banner">{error}</p>}
        {papers.length > 0 && <div className="paper-list"><div className="section-heading"><h2>Recent papers</h2><span>{papers.length}</span></div>{papers.map((paper) => <button className="paper-row" key={paper.id} onClick={() => openPaper(paper)}><span className="paper-badge">PDF</span><span><strong>{paper.original_name}</strong><small>{paper.page_count} pages · stored locally</small></span><span className="row-arrow">→</span></button>)}</div>}
      </section>
    </main>
  );

  return (
    <main className="reader-shell">
      <header className="reader-header"><button className="brand-button" onClick={() => setActive(null)} aria-label="Back to library"><Brand /></button><div className="document-title"><strong>{active.original_name.replace(/\.pdf$/i, '')}</strong><span>{pages || active.page_count} pages · local</span></div><div className="paper-zoom"><span className="control-label">Paper</span><div className="header-actions" role="group" aria-label="Paper zoom"><button title="Zoom paper out" aria-label="Zoom paper out" disabled={zoom <= MIN_ZOOM} onClick={() => changeZoom(-.1)}>−</button><span>{Math.round(zoom * 100)}%</span><button title="Zoom paper in" aria-label="Zoom paper in" disabled={zoom >= MAX_ZOOM} onClick={() => changeZoom(.1)}>+</button></div></div></header>
      <div className="reader-workspace">
        <section className={`pdf-pane${screenshotMode ? ' screenshot-mode' : ''}`} ref={viewerRef} onMouseUp={(event) => { if (!screenshotMode) captureSelection(event); }} onPointerDown={beginScreenshot} onPointerMove={moveScreenshot} onPointerUp={finishScreenshot} onPointerCancel={() => { screenshotPointerRef.current = null; setScreenshotDrag(null); setScreenshotMode(false); }}>
          <PdfDocument file={`${API_BASE}/api/documents/${active.id}/file`} onLoadSuccess={({ numPages }) => setPages(numPages)} loading={<div className="viewer-message">Rendering paper…</div>} error={<div className="viewer-message error-banner">Could not render this PDF.</div>}>
            {Array.from({ length: pages }, (_, index) => <div className="pdf-page-stage" data-page-number={index + 1} key={index + 1}><div className="pdf-page-wrap" style={{ zoom: zoom / MAX_ZOOM }}><PdfPageWithHighlights pageNumber={index + 1} highlights={highlightEntries.filter((entry) => entry.page === index + 1)} /><span className="page-label">{index + 1}</span></div></div>)}
          </PdfDocument>
          {screenshotMode && !screenshotDrag && <div className="screenshot-hint">Drag over the PDF to add an image · Esc to cancel</div>}
          {screenshotDrag && <div className="screenshot-region" style={{ left: screenshotDrag.overlayLeft, top: screenshotDrag.overlayTop, width: screenshotDrag.overlayWidth, height: screenshotDrag.overlayHeight }} />}
        </section>
        <aside className="side-pane" style={{ '--chat-scale': chatScale } as CSSProperties}>
          <div className="mode-tabs"><div className="tab-list"><button className="active">Chat</button><button disabled>Notes <span>Soon</span></button></div><div className="chat-text-controls" role="group" aria-label="Chat text size"><span className="control-label">Text size</span><div><button type="button" aria-label="Decrease chat text size" title="Decrease chat text size" disabled={chatScale <= MIN_CHAT_SCALE} onClick={() => changeChatScale(-CHAT_SCALE_STEP)}>A−</button><output aria-live="polite" aria-label={`Chat text size ${Math.round(chatScale * 100)} percent`}>{Math.round(chatScale * 100)}%</output><button type="button" aria-label="Increase chat text size" title="Increase chat text size" disabled={chatScale >= MAX_CHAT_SCALE} onClick={() => changeChatScale(CHAT_SCALE_STEP)}>A+</button></div></div></div>
          <div className="chat-body" ref={chatBodyRef} onWheelCapture={(event) => { if (event.deltaY < 0) pauseChatFollow(); }} onTouchMove={pauseChatFollow} onPointerDown={(event) => { if (event.target === event.currentTarget) pauseChatFollow(); }} onScroll={(event) => { const element = event.currentTarget; followOutputRef.current = element.scrollHeight - element.scrollTop - element.clientHeight < 40; }}><div className="chat-heading"><span className="spark">✦</span><div><strong>Ask about this paper</strong><p>Select text in the paper to give the model precise context.</p></div></div>
            {chatHistory.map((turn) => <div className="chat-turn" key={turn.id}><ContextList selections={turn.context} /><div className="user-message"><span>You</span><p>{turn.question}</p></div><div className="answer-card complete"><div className="answer-meta"><span>Adam</span><span className="stream-state">Done</span></div><MarkdownAnswer>{turn.answer}</MarkdownAnswer></div></div>)}
            {submittedQuestion && <><ContextList selections={submittedContext} /><div className="user-message"><span>You</span><p>{submittedQuestion}</p></div></>}
            {streamStatus !== 'idle' && streamStatus !== 'error' && <div className={`answer-card ${streamStatus}`} aria-live="polite"><div className="answer-meta"><span>Adam</span><span className="stream-state">{streamStatus === 'connecting' ? <>Thinking<span className="thinking-dots"><i /><i /><i /></span></> : streamStatus === 'streaming' ? 'Responding…' : 'Done'}</span></div>{answer ? <MarkdownAnswer streaming={streamStatus === 'streaming'}>{answer}</MarkdownAnswer> : <div className="answer-skeleton"><i /><i /><i /></div>}</div>}{error && <p className="error-banner compact">{error}</p>}
            {contextSelections.length > 0 ? <ContextList selections={contextSelections} onRemove={(id) => setContextSelections((current) => current.filter((item) => item.id !== id))} /> : !submittedQuestion && <div className="empty-context"><span>⌁</span><p>Highlight a passage, then add it to context.</p></div>}
          </div>
          <form className="composer" onSubmit={ask}><textarea ref={composerRef} value={question} onChange={(event) => setQuestion(event.target.value)} onKeyDown={(event) => { if (event.key === 'Enter' && !event.shiftKey && !event.nativeEvent.isComposing) { event.preventDefault(); event.currentTarget.form?.requestSubmit(); } }} placeholder={contextSelections.length ? 'Ask about your context…' : canAsk ? 'Ask a follow-up…' : 'Highlight text or press R to capture an area…'} disabled={!canAsk || asking} rows={3} /><div><span>{contextSelections.length ? `${contextSelections.length} context ${contextSelections.length === 1 ? 'item' : 'items'}` : canAsk ? 'Using conversation context' : 'Press R for screenshot'}</span><button type="submit" disabled={!canAsk || !question.trim() || asking}>{asking ? '…' : '↑'}</button></div></form>
        </aside>
      </div>
      {pendingSelection && <div className="selection-toolbar" style={{ left: pendingSelection.x, top: pendingSelection.y }} onMouseDown={(event) => event.preventDefault()} role="toolbar" aria-label="Text selection actions"><div className="highlight-colors" aria-label="Highlight color">{HIGHLIGHT_COLORS.map(({ color, label, key }) => <button type="button" className="color-swatch" style={{ backgroundColor: color }} aria-label={`Highlight ${label.toLowerCase()} (${key})`} aria-keyshortcuts={key} title={`${label} highlight · ${key}`} onClick={() => applyHighlight(color)} key={color}><kbd>{key}</kbd></button>)}</div><span className="toolbar-divider" /><button type="button" className="toolbar-action primary" aria-keyshortcuts="C" onClick={addSelectionToContext}><span>＋</span>Add to context <kbd>C</kbd></button><button type="button" className="toolbar-action" disabled title="Coming soon"><span>✦</span>Ask <small>Beta</small></button><button type="button" className="toolbar-action" disabled title="Coming soon"><span>▱</span>Note <small>Beta</small></button></div>}
    </main>
  );
}

function formatContext(selections: ContextSelection[]) {
  return selections.filter((item) => !item.imageDataUrl).map((item, index) => `[Excerpt ${index + 1}${item.page ? `, page ${item.page}` : ''}]\n${item.text}`).join('\n\n');
}

function formatImages(selections: ContextSelection[]) {
  return selections.flatMap((item) => item.imageDataUrl ? [{ data_url: item.imageDataUrl, page: item.page }] : []);
}

function ContextList({ selections, onRemove }: { selections: ContextSelection[]; onRemove?: (id: string) => void }) {
  if (selections.length === 0) return null;
  return <div className="context-list"><div className="context-list-heading"><span>Context</span><small>{selections.length} {selections.length === 1 ? 'item' : 'items'}</small></div>{selections.map((selection, index) => <div className={`selection-card${selection.imageDataUrl ? ' image-context' : ''}`} key={selection.id}><div><span>{selection.imageDataUrl ? 'Screenshot' : 'Excerpt'} {index + 1}{selection.page ? ` · ${selection.pageEnd && selection.pageEnd !== selection.page ? `pages ${selection.page}–${selection.pageEnd}` : `page ${selection.page}`}` : ''}</span>{onRemove && <button type="button" aria-label={`Remove context item ${index + 1}`} onClick={() => onRemove(selection.id)}>×</button>}</div>{selection.imageDataUrl ? <img src={selection.imageDataUrl} alt={`Selected area from page ${selection.page ?? ''}`} /> : <blockquote>{selection.text}</blockquote>}</div>)}</div>;
}

function PdfPageWithHighlights({ pageNumber, highlights }: { pageNumber: number; highlights: HighlightEntry[] }) {
  const [renderVersion, setRenderVersion] = useState(0);
  const pageElementRef = useRef<HTMLDivElement>(null);
  const pdfPageRef = useRef<PDFPageProxy | null>(null);
  const textContentRef = useRef<TextContent | null>(null);
  const handleRenderSuccess = useCallback(() => setRenderVersion((version) => version + 1), []);
  const alignTextLayer = useCallback(() => {
    const element = pageElementRef.current;
    const page = pdfPageRef.current;
    const content = textContentRef.current;
    if (!element || !page || !content) return;
    const viewport = page.getViewport({ scale: 1 });
    // PDF.js measures fonts on a separate canvas. Browser zoom and font
    // substitution can make those metrics differ from the actual DOM glyphs.
    // Match each text run to its PDF width using the displayed page scale.
    const screenScale = element.getBoundingClientRect().width / viewport.width;
    const spans = element.querySelectorAll<HTMLElement>('.textLayer span[role="presentation"]');
    const items = content.items.filter((item) => 'str' in item && item.str.length > 0);
    if (spans.length !== items.length || screenScale <= 0) return;
    spans.forEach((span, index) => {
      const item = items[index];
      if (!('str' in item) || span.textContent !== item.str || content.styles[item.fontName]?.vertical) return;
      const transform = pdfjs.Util.transform(viewport.transform, item.transform);
      // Leave rotated runs to PDF.js: their bounding width includes height.
      if (Math.abs(transform[1]) > 0.001 || item.width <= 0) return;
      const actualWidth = span.getBoundingClientRect().width;
      const expectedWidth = item.width * viewport.scale * screenScale;
      if (actualWidth > 0 && Math.abs(actualWidth - expectedWidth) > 0.25) {
        span.style.transform = `${span.style.transform} scaleX(${expectedWidth / actualWidth})`;
      }
    });
  }, []);
  return <Page inputRef={pageElementRef} pageNumber={pageNumber} width={BASE_PAGE_WIDTH * MAX_ZOOM} renderAnnotationLayer renderTextLayer onLoadSuccess={(page) => { pdfPageRef.current = page; }} onGetTextSuccess={(content) => { textContentRef.current = content; }} onRenderTextLayerSuccess={alignTextLayer} onRenderSuccess={handleRenderSuccess}><HighlightCanvas highlights={highlights} renderVersion={renderVersion} /></Page>;
}

function HighlightCanvas({ highlights, renderVersion }: { highlights: HighlightEntry[]; renderVersion: number }) {
  const overlayRef = useRef<HTMLCanvasElement>(null);
  useEffect(() => {
    const overlay = overlayRef.current;
    const page = overlay?.closest<HTMLElement>('.react-pdf__Page');
    const source = page?.querySelector<HTMLCanvasElement>('.react-pdf__Page__canvas');
    if (!overlay || !source || !source.width || !source.height) return;
    overlay.width = source.width;
    overlay.height = source.height;
    const sourceContext = source.getContext('2d', { willReadFrequently: true });
    const overlayContext = overlay.getContext('2d');
    if (!sourceContext || !overlayContext) return;
    overlayContext.clearRect(0, 0, overlay.width, overlay.height);
    highlights.forEach((highlight) => {
      const red = Number.parseInt(highlight.color.slice(1, 3), 16);
      const green = Number.parseInt(highlight.color.slice(3, 5), 16);
      const blue = Number.parseInt(highlight.color.slice(5, 7), 16);
      highlight.rects.forEach((rect) => {
        const left = Math.max(0, Math.floor(rect.left * source.width));
        const top = Math.max(0, Math.floor(rect.top * source.height));
        const width = Math.min(source.width - left, Math.ceil(rect.width * source.width));
        const height = Math.min(source.height - top, Math.ceil(rect.height * source.height));
        if (width <= 0 || height <= 0) return;
        const sourcePixels = sourceContext.getImageData(left, top, width, height).data;
        const marker = overlayContext.createImageData(width, height);
        for (let pixel = 0; pixel < sourcePixels.length; pixel += 4) {
          const luminance = sourcePixels[pixel] * .2126 + sourcePixels[pixel + 1] * .7152 + sourcePixels[pixel + 2] * .0722;
          const backgroundAmount = Math.max(0, Math.min(1, (luminance - 190) / 55));
          marker.data[pixel] = red;
          marker.data[pixel + 1] = green;
          marker.data[pixel + 2] = blue;
          marker.data[pixel + 3] = Math.round(174 * backgroundAmount);
        }
        overlayContext.putImageData(marker, left, top);
      });
    });
  }, [highlights, renderVersion]);
  return <canvas className="pdf-highlight-canvas" ref={overlayRef} aria-hidden="true" />;
}

function Brand() { return <div className="brand"><span className="brand-mark">A</span><strong>Adam</strong></div>; }
function MarkdownAnswer({ children, streaming = false }: { children: string; streaming?: boolean }) {
  return <div className="markdown-answer"><ReactMarkdown remarkPlugins={[remarkGfm, remarkMath]} rehypePlugins={[rehypeKatex]} components={{ a: ({ children: linkText, ...props }) => <a {...props} target="_blank" rel="noreferrer">{linkText}</a> }}>{children}</ReactMarkdown>{streaming && <i className="stream-cursor" />}</div>;
}
function UploadButton({ uploading, upload }: { uploading: boolean; upload: (file: File) => Promise<void> }) { return <label className="primary-button">{uploading ? 'Opening…' : 'Open PDF'}<input type="file" accept="application/pdf" hidden disabled={uploading} onChange={(event) => event.target.files?.[0] && void upload(event.target.files[0])} /></label>; }

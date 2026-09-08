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
type QuickAskTarget = ContextSelection & { x: number; y: number };
type ScreenshotDrag = { startContentX: number; startContentY: number; currentContentX: number; currentContentY: number; overlayLeft: number; overlayTop: number; overlayWidth: number; overlayHeight: number };
type HighlightRect = { left: number; top: number; width: number; height: number };
type HighlightEntry = ContextSelection & { color: string; range: Range | null; rects: HighlightRect[] };
type StreamStatus = 'idle' | 'connecting' | 'streaming' | 'complete' | 'error';
type ChatTurn = { id: string; question: string; answer: string; context: ContextSelection[]; importedQuickAsk?: boolean };
type QuickTurn = { question: string; answer: string };
type Conversation = { id: string; document_id: string; title: string; provider: string; model_id: string; context_builder_version: string; updated_at: string; message_count: number };
type SavedMessage = { id: string; role: string; content: string; context_json: string | null };
type ProviderId = 'zen' | 'openrouter' | 'openai' | 'anthropic' | 'google';
type AppSettings = { provider: ProviderId; model: string; selected_models: Partial<Record<ProviderId, string>>; providers: Record<ProviderId, boolean>; favorites: Partial<Record<ProviderId, string[]>>; system_prompt: string; quick_ask_prompt: string };
const PROVIDERS: Array<{ id: ProviderId; name: string; keyLabel: string }> = [
  { id: 'zen', name: 'OpenCode Zen', keyLabel: 'OpenCode Zen key' },
  { id: 'openrouter', name: 'OpenRouter', keyLabel: 'OpenRouter key' },
  { id: 'openai', name: 'OpenAI', keyLabel: 'OpenAI key' },
  { id: 'anthropic', name: 'Anthropic', keyLabel: 'Anthropic key' },
  { id: 'google', name: 'Google', keyLabel: 'Google AI key' },
];
const API_BASE = '';
const MIN_ZOOM = .6;
const MAX_ZOOM = 1.8;
const DEFAULT_ZOOM = MAX_ZOOM;
const MIN_CHAT_SCALE = .85;
const MAX_CHAT_SCALE = 1.35;
const CHAT_SCALE_STEP = .1;
const DEFAULT_CHAT_SCALE = 1.2;
const BASE_PAGE_WIDTH = 760;
const DEFAULT_PAPER_PERCENT = 68;
const MIN_PAPER_PERCENT = 52;
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
  const [quickAskTarget, setQuickAskTarget] = useState<QuickAskTarget | null>(null);
  const [quickQuestion, setQuickQuestion] = useState('');
  const [quickActiveQuestion, setQuickActiveQuestion] = useState('');
  const [quickAnswer, setQuickAnswer] = useState('');
  const [quickTurns, setQuickTurns] = useState<QuickTurn[]>([]);
  const [quickAsking, setQuickAsking] = useState(false);
  const [quickImporting, setQuickImporting] = useState(false);
  const [quickError, setQuickError] = useState('');
  const [screenshotMode, setScreenshotMode] = useState(false);
  const [screenshotDrag, setScreenshotDrag] = useState<ScreenshotDrag | null>(null);
  const [highlightEntries, setHighlightEntries] = useState<HighlightEntry[]>([]);
  const [question, setQuestion] = useState('');
  const [submittedQuestion, setSubmittedQuestion] = useState('');
  const [submittedContext, setSubmittedContext] = useState<ContextSelection[]>([]);
  const [answer, setAnswer] = useState('');
  const [chatHistory, setChatHistory] = useState<ChatTurn[]>([]);
  const [conversations, setConversations] = useState<Conversation[]>([]);
  const [activeConversation, setActiveConversation] = useState<Conversation | null>(null);
  const [historyOpen, setHistoryOpen] = useState(false);
  const [streamStatus, setStreamStatus] = useState<StreamStatus>('idle');
  const [zoom, setZoom] = useState(DEFAULT_ZOOM);
  const [chatScale, setChatScale] = useState(DEFAULT_CHAT_SCALE);
  const [paperPercent, setPaperPercent] = useState(DEFAULT_PAPER_PERCENT);
  const [uploading, setUploading] = useState(false);
  const [renaming, setRenaming] = useState(false);
  const [renameValue, setRenameValue] = useState('');
  const [renameSaving, setRenameSaving] = useState(false);
  const [renamingChatId, setRenamingChatId] = useState<string | null>(null);
  const [chatTitleValue, setChatTitleValue] = useState('');
  const [titleSaving, setTitleSaving] = useState(false);
  const [asking, setAsking] = useState(false);
  const [error, setError] = useState('');
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [chatConfigured, setChatConfigured] = useState<boolean | null>(null);
  const viewerRef = useRef<HTMLDivElement>(null);
  const workspaceRef = useRef<HTMLDivElement>(null);
  const resizingRef = useRef(false);
  const chatBodyRef = useRef<HTMLDivElement>(null);
  const composerRef = useRef<HTMLTextAreaElement>(null);
  const quickAskInputRef = useRef<HTMLInputElement>(null);
  const quickThreadRef = useRef<HTMLDivElement>(null);
  const quickFollowRef = useRef(true);
  const quickDragRef = useRef<{ pointerId: number; offsetX: number; offsetY: number } | null>(null);
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

  const fitPaperToPane = useCallback(() => {
    const viewer = viewerRef.current;
    if (!viewer) return;
    const styles = window.getComputedStyle(viewer);
    const horizontalPadding = parseFloat(styles.paddingLeft) + parseFloat(styles.paddingRight);
    const availableWidth = viewer.clientWidth - horizontalPadding;
    if (availableWidth <= 0) return;
    const fittedZoom = Math.min(MAX_ZOOM, Math.max(MIN_ZOOM, availableWidth / BASE_PAGE_WIDTH));
    changeZoom(fittedZoom - zoomRef.current);
  }, [changeZoom]);

  const changeChatScale = useCallback((delta: number) => {
    setChatScale((current) => Math.min(MAX_CHAT_SCALE, Math.max(MIN_CHAT_SCALE, Number((current + delta).toFixed(2)))));
  }, []);

  async function syncConversationDefaults(conversation: Conversation) {
    const response = await fetch(`${API_BASE}/api/conversations/${conversation.id}/sync-defaults`, { method: 'POST' });
    if (!response.ok) return conversation;
    const updated = await response.json() as Conversation;
    setActiveConversation((current) => current?.id === updated.id ? updated : current);
    setConversations((current) => current.map((item) => item.id === updated.id ? updated : item));
    return updated;
  }

  useEffect(() => {
    // The initial library fetch intentionally synchronizes remote state after mount.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    void loadPapers();
    void fetch(`${API_BASE}/api/settings`).then((response) => response.json()).then((value: AppSettings) => setChatConfigured(Boolean(value.providers[value.provider]))).catch(() => setChatConfigured(false));
  }, [loadPapers]);

  useEffect(() => {
    const stored = Number(window.localStorage.getItem('adam.paperPercent'));
    // The persisted split is external browser state restored after hydration.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    if (Number.isFinite(stored) && stored >= MIN_PAPER_PERCENT && stored <= 80) setPaperPercent(stored);
  }, []);

  useLayoutEffect(() => {
    const viewer = viewerRef.current;
    if (!viewer || !active) return;
    fitPaperToPane();
    const observer = new ResizeObserver(fitPaperToPane);
    observer.observe(viewer);
    return () => observer.disconnect();
  }, [active, fitPaperToPane]);

  const closeSettings = useCallback(() => {
    setSettingsOpen(false);
    void fetch(`${API_BASE}/api/settings`).then((response) => response.json()).then((value: AppSettings) => setChatConfigured(Boolean(value.providers[value.provider]))).catch(() => setChatConfigured(false));
    if (activeConversation?.message_count === 0) void syncConversationDefaults(activeConversation);
  }, [activeConversation]);

  useEffect(() => {
    const openSettings = (event: KeyboardEvent) => {
      if ((event.metaKey || event.ctrlKey) && event.key === ',') {
        event.preventDefault();
        setSettingsOpen(true);
      }
    };
    window.addEventListener('keydown', openSettings);
    return () => window.removeEventListener('keydown', openSettings);
  }, []);

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

  function resizeWorkspace(clientX: number) {
    const workspace = workspaceRef.current;
    if (!workspace) return;
    const rect = workspace.getBoundingClientRect();
    const minimumChatPixels = 340;
    const maximum = Math.min(80, 100 - minimumChatPixels / rect.width * 100);
    const next = Math.min(maximum, Math.max(MIN_PAPER_PERCENT, (clientX - rect.left) / rect.width * 100));
    const rounded = Number(next.toFixed(2));
    setPaperPercent(rounded);
    window.localStorage.setItem('adam.paperPercent', String(rounded));
  }

  function beginWorkspaceResize(event: React.PointerEvent<HTMLDivElement>) {
    if (event.button !== 0) return;
    event.preventDefault();
    resizingRef.current = true;
    event.currentTarget.setPointerCapture(event.pointerId);
    resizeWorkspace(event.clientX);
  }

  function moveWorkspaceResize(event: React.PointerEvent<HTMLDivElement>) {
    if (resizingRef.current) resizeWorkspace(event.clientX);
  }

  function finishWorkspaceResize(event: React.PointerEvent<HTMLDivElement>) {
    if (!resizingRef.current) return;
    resizingRef.current = false;
    event.currentTarget.releasePointerCapture(event.pointerId);
  }

  function resetWorkspaceResize() {
    setPaperPercent(DEFAULT_PAPER_PERCENT);
    window.localStorage.setItem('adam.paperPercent', String(DEFAULT_PAPER_PERCENT));
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

  async function deletePaper(paper: Paper) {
    const confirmed = window.confirm(`Permanently delete “${paper.original_name}”?\n\nThis will remove the PDF, all chats, messages, highlights, and other data associated with this paper. This cannot be undone.`);
    if (!confirmed) return;
    try {
      const response = await fetch(`${API_BASE}/api/documents/${paper.id}`, { method: 'DELETE' });
      if (!response.ok) {
        const payload = await response.json().catch(() => ({}));
        throw new Error(payload.detail ?? 'Could not delete this paper.');
      }
      setPapers((current) => current.filter((item) => item.id !== paper.id));
      window.localStorage.removeItem(`adam.conversation.${paper.id}`);
      if (active?.id === paper.id) {
        window.localStorage.removeItem('adam.activePaper');
        setActive(null);
        setActiveConversation(null);
        setConversations([]);
      }
      setError('');
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Could not delete this paper.');
    }
  }

  async function renamePaper(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!active || renameSaving || !renameValue.trim()) return;
    setRenameSaving(true);
    setError('');
    try {
      const response = await fetch(`${API_BASE}/api/documents/${active.id}`, {
        method: 'PATCH', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name: renameValue.trim() }),
      });
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.detail ?? 'Could not rename this PDF.');
      const renamed = payload as Paper;
      setActive(renamed);
      setPapers((current) => current.map((paper) => paper.id === renamed.id ? renamed : paper));
      setRenaming(false);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Could not rename this PDF.');
    } finally {
      setRenameSaving(false);
    }
  }

  function openPaper(paper: Paper) {
    window.localStorage.setItem('adam.activePaper', paper.id);
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
    setConversations([]);
    setActiveConversation(null);
    setHistoryOpen(false);
    setStreamStatus('idle');
    zoomRef.current = DEFAULT_ZOOM;
    setZoom(DEFAULT_ZOOM);
    setError('');
    void loadAnnotations(paper.id);
    void loadConversations(paper.id);
  }

  async function loadConversations(documentId: string) {
    try {
      let response = await fetch(`${API_BASE}/api/documents/${documentId}/conversations`);
      if (!response.ok) throw new Error('Could not load saved chats.');
      let items = await response.json() as Conversation[];
      if (items.length === 0) {
        response = await fetch(`${API_BASE}/api/documents/${documentId}/conversations`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}' });
        if (!response.ok) throw new Error('Could not create a chat.');
        items = [await response.json() as Conversation];
      }
      setConversations(items);
      const preferred = window.localStorage.getItem(`adam.conversation.${documentId}`);
      await openConversation(items.find((item) => item.id === preferred) ?? items[0]);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Could not load saved chats.');
    }
  }

  async function openConversation(conversation: Conversation) {
    const response = await fetch(`${API_BASE}/api/conversations/${conversation.id}`);
    if (!response.ok) throw new Error('Could not open this chat.');
    const detail = await response.json() as Conversation & { messages: SavedMessage[] };
    window.localStorage.setItem(`adam.conversation.${conversation.document_id}`, conversation.id);
    setActiveConversation(conversation);
    setHistoryOpen(false);
    const turns: ChatTurn[] = [];
    for (let index = 0; index < detail.messages.length; index += 1) {
      const user = detail.messages[index];
      const assistant = detail.messages[index + 1];
      if (user.role !== 'user' || assistant?.role !== 'assistant') continue;
      turns.push({ id: user.id, question: user.content, answer: assistant.content, context: contextFromJson(user.context_json), importedQuickAsk: contextScope(user.context_json) === 'quick_ask_saved' });
      index += 1;
    }
    setChatHistory(turns);
    setSubmittedQuestion(''); setSubmittedContext([]); setAnswer(''); setStreamStatus('idle'); setContextSelections([]); setError('');
  }

  async function createNewConversation() {
    if (!active) return;
    const response = await fetch(`${API_BASE}/api/documents/${active.id}/conversations`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}' });
    if (!response.ok) { setError('Could not create a chat.'); return; }
    const conversation = await response.json() as Conversation;
    setConversations((current) => [conversation, ...current]);
    await openConversation(conversation);
  }

  async function deleteConversation(conversation: Conversation) {
    if (!window.confirm(`Delete “${conversation.title}”?`)) return;
    const response = await fetch(`${API_BASE}/api/conversations/${conversation.id}`, { method: 'DELETE' });
    if (!response.ok) { setError('Could not delete this chat.'); return; }
    const remaining = conversations.filter((item) => item.id !== conversation.id);
    setConversations(remaining);
    if (activeConversation?.id === conversation.id) {
      if (remaining[0]) await openConversation(remaining[0]); else await createNewConversation();
      setHistoryOpen(true);
    }
  }

  function beginChatRename(conversation: Conversation) {
    setRenamingChatId(conversation.id);
    setChatTitleValue(conversation.title);
  }

  async function renameConversation(event: FormEvent<HTMLFormElement>, conversation: Conversation) {
    event.preventDefault();
    const title = chatTitleValue.trim();
    if (!title || titleSaving) return;
    setTitleSaving(true); setError('');
    try {
      const response = await fetch(`${API_BASE}/api/conversations/${conversation.id}`, { method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ title }) });
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.detail ?? 'Could not rename this chat.');
      const updated = payload as Conversation;
      setConversations((current) => current.map((item) => item.id === updated.id ? { ...item, ...updated } : item));
      setActiveConversation((current) => current?.id === updated.id ? { ...current, ...updated } : current);
      setRenamingChatId(null);
    } catch (reason) { setError(reason instanceof Error ? reason.message : 'Could not rename this chat.'); }
    finally { setTitleSaving(false); }
  }

  async function regenerateConversationTitle(conversation: Conversation) {
    if (titleSaving) return;
    setTitleSaving(true); setError('');
    try {
      const response = await fetch(`${API_BASE}/api/conversations/${conversation.id}/regenerate-title`, { method: 'POST' });
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.detail ?? 'Could not regenerate this title.');
      const updated = payload as Conversation;
      setConversations((current) => current.map((item) => item.id === updated.id ? { ...item, ...updated } : item));
      setActiveConversation((current) => current?.id === updated.id ? { ...current, ...updated } : current);
    } catch (reason) { setError(reason instanceof Error ? reason.message : 'Could not regenerate this title.'); }
    finally { setTitleSaving(false); }
  }

  useEffect(() => {
    if (active || papers.length === 0) return;
    const saved = papers.find((paper) => paper.id === window.localStorage.getItem('adam.activePaper'));
    if (saved) openPaper(saved);
    // Workspace restoration intentionally runs only after the library changes.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [papers]);

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

  function openQuickAskFromSelection() {
    if (!pendingSelection) return;
    const { id, text, page, x, y } = pendingSelection;
    setQuickAskTarget({ id, text, page, x, y: Math.min(window.innerHeight - 500, Math.max(70, y + 12)) });
    setQuickQuestion(''); setQuickActiveQuestion(''); setQuickAnswer(''); setQuickTurns([]); setQuickError('');
    clearBrowserSelection();
    window.requestAnimationFrame(() => quickAskInputRef.current?.focus());
  }

  function closeQuickAsk() {
    if (quickAsking) return;
    setQuickAskTarget(null); setQuickQuestion(''); setQuickActiveQuestion(''); setQuickAnswer(''); setQuickTurns([]); setQuickError('');
  }

  useEffect(() => {
    if (quickAskTarget) quickAskInputRef.current?.focus({ preventScroll: true });
  }, [quickAskTarget]);

  function addQuickTargetToContext() {
    if (!quickAskTarget) return;
    const selection: ContextSelection = { id: quickAskTarget.id, text: quickAskTarget.text, page: quickAskTarget.page, pageEnd: quickAskTarget.pageEnd, imageDataUrl: quickAskTarget.imageDataUrl };
    setContextSelections((current) => current.some((item) => item.id === selection.id) ? current : [...current, selection]);
    revealNewContext();
    setQuickAskTarget(null);
  }

  async function submitQuickAsk(event: FormEvent) {
    event.preventDefault();
    if (!quickAskTarget || !activeConversation || !quickQuestion.trim() || quickAsking) return;
    const priorTurns = quickAnswer ? [...quickTurns, { question: quickActiveQuestion, answer: quickAnswer }] : quickTurns;
    const sentQuestion = quickQuestion.trim();
    if (quickAnswer) setQuickTurns(priorTurns);
    quickFollowRef.current = true;
    setQuickAsking(true); setQuickActiveQuestion(sentQuestion); setQuickQuestion(''); setQuickAnswer(''); setQuickError('');
    try {
      const selectionText = quickAskTarget.imageDataUrl ? '' : quickAskTarget.text;
      const selectionImages = quickAskTarget.imageDataUrl ? [{ data_url: quickAskTarget.imageDataUrl, page: quickAskTarget.page }] : [];
      const response = await fetch(`${API_BASE}/api/conversations/${activeConversation.id}/quick-ask/stream`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ question: sentQuestion, selected_text: priorTurns.length ? '' : selectionText, images: priorTurns.length ? [] : selectionImages, page: quickAskTarget.page, history: priorTurns.map((turn, index) => ({ ...turn, selected_text: index === 0 ? selectionText : '', images: index === 0 ? selectionImages : [], page: quickAskTarget.page })) }) });
      if (!response.ok || !response.body) { const payload = await response.json().catch(() => ({})); throw new Error(payload.detail ?? 'Quick Ask failed.'); }
      const reader = response.body.getReader(); const decoder = new TextDecoder(); let buffer = '';
      while (true) {
        const { value, done } = await reader.read(); if (done) break;
        buffer += decoder.decode(value, { stream: true }); const events = buffer.split('\n\n'); buffer = events.pop() ?? '';
        for (const block of events) { const line = block.split('\n').find((item) => item.startsWith('data: ')); if (!line) continue; const data = JSON.parse(line.slice(6)); if (data.type === 'delta') setQuickAnswer((current) => current + data.text); if (data.type === 'error') throw new Error(data.message); }
      }
    } catch (reason) { setQuickError(reason instanceof Error ? reason.message : 'Quick Ask failed.'); }
    finally { setQuickAsking(false); }
  }

  useLayoutEffect(() => {
    const thread = quickThreadRef.current;
    if (!thread || !quickFollowRef.current) return;
    thread.scrollTop = thread.scrollHeight;
  }, [quickAnswer, quickTurns, quickAsking]);

  function beginQuickDrag(event: React.PointerEvent<HTMLDivElement>) {
    if (!quickAskTarget || (event.target as HTMLElement).closest('button')) return;
    event.currentTarget.setPointerCapture(event.pointerId);
    quickDragRef.current = { pointerId: event.pointerId, offsetX: event.clientX - quickAskTarget.x, offsetY: event.clientY - quickAskTarget.y };
  }

  function moveQuickDrag(event: React.PointerEvent<HTMLDivElement>) {
    const drag = quickDragRef.current; if (!drag || drag.pointerId !== event.pointerId) return;
    setQuickAskTarget((target) => target && ({ ...target, x: Math.min(window.innerWidth - 220, Math.max(220, event.clientX - drag.offsetX)), y: Math.min(window.innerHeight - 90, Math.max(10, event.clientY - drag.offsetY)) }));
  }

  function endQuickDrag(event: React.PointerEvent<HTMLDivElement>) {
    if (quickDragRef.current?.pointerId === event.pointerId) quickDragRef.current = null;
  }

  async function importQuickAsk() {
    if (!quickAskTarget || !activeConversation || quickAsking || quickImporting) return;
    const turns = quickAnswer ? [...quickTurns, { question: quickActiveQuestion, answer: quickAnswer }] : quickTurns;
    if (!turns.length) return;
    setQuickImporting(true); setQuickError('');
    try {
      const response = await fetch(`${API_BASE}/api/conversations/${activeConversation.id}/quick-ask/import`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ selected_text: quickAskTarget.imageDataUrl ? '' : quickAskTarget.text, images: quickAskTarget.imageDataUrl ? [{ data_url: quickAskTarget.imageDataUrl, page: quickAskTarget.page }] : [], page: quickAskTarget.page, turns: turns.map((turn) => ({ ...turn, selected_text: '', images: [], page: quickAskTarget.page })) }) });
      const updated = await response.json() as Conversation; if (!response.ok) throw new Error((updated as unknown as { detail?: string }).detail ?? 'Could not move Quick Ask to chat.');
      setActiveConversation(updated); setConversations((current) => [updated, ...current]);
      await openConversation(updated); setQuickAskTarget(null); setQuickQuestion(''); setQuickActiveQuestion(''); setQuickAnswer(''); setQuickTurns([]);
    } catch (reason) { setQuickError(reason instanceof Error ? reason.message : 'Could not move Quick Ask to chat.'); }
    finally { setQuickImporting(false); }
  }

  useEffect(() => {
    if (!quickAskTarget) return;
    const handleQuickAskHotkey = (event: KeyboardEvent) => {
      const target = event.target as HTMLElement | null;
      if (event.key === 'Escape') { event.preventDefault(); closeQuickAsk(); return; }
      if (event.key.toLowerCase() === 'c' && !event.metaKey && !event.ctrlKey && !event.altKey && !target?.matches('input, textarea, select') && !target?.isContentEditable) { event.preventDefault(); addQuickTargetToContext(); }
    };
    window.addEventListener('keydown', handleQuickAskHotkey);
    return () => window.removeEventListener('keydown', handleQuickAskHotkey);
  });

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
    setQuickAskTarget({ id: crypto.randomUUID(), text: 'Selected PDF area', page, pageEnd, imageDataUrl: output.toDataURL('image/jpeg', .92), x: Math.min(window.innerWidth - 220, Math.max(220, event.clientX)), y: Math.min(window.innerHeight - 500, Math.max(70, event.clientY - 12)) });
    setQuickQuestion(''); setQuickActiveQuestion(''); setQuickAnswer(''); setQuickTurns([]); setQuickError('');
    window.requestAnimationFrame(() => quickAskInputRef.current?.focus());
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
      } else if (event.key.toLowerCase() === 'a') {
        event.preventDefault();
        openQuickAskFromSelection();
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
    if (!active || !activeConversation || !question.trim() || asking) return;
    const sendingConversation = activeConversation.message_count === 0 ? await syncConversationDefaults(activeConversation) : activeConversation;
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
      const response = await fetch(`${API_BASE}/api/conversations/${sendingConversation.id}/messages/stream`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ question: sentQuestion, selected_text: formatContext(sentContext), images: formatImages(sentContext), page: sentContext.length === 1 ? sentContext[0].page : null }),
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
          if (data.type === 'started') {
            setStreamStatus('connecting');
            if (data.title) {
              setActiveConversation((current) => current?.id === sendingConversation.id ? { ...current, title: data.title } : current);
              setConversations((current) => current.map((item) => item.id === sendingConversation.id ? { ...item, title: data.title } : item));
            }
          }
          if (data.type === 'delta') answerQueueRef.current += data.text;
          if (data.type === 'completed') streamFinishedRef.current = true;
          if (data.type === 'error') throw new Error(data.message);
        }
      }
      streamFinishedRef.current = true;
      const savedConversation = await fetch(`${API_BASE}/api/conversations/${sendingConversation.id}`).then((result) => result.ok ? result.json() as Promise<Conversation> : null).catch(() => null);
      const updatedConversation = { ...sendingConversation, ...(savedConversation ?? {}), message_count: sendingConversation.message_count + 2, updated_at: new Date().toISOString() };
      setActiveConversation(updatedConversation);
      setConversations((current) => current.map((item) => item.id === updatedConversation.id ? updatedConversation : item));
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'The model request failed.');
      streamFinishedRef.current = false;
      setStreamStatus('error');
      setAsking(false);
      if (revealTimerRef.current !== null) window.clearInterval(revealTimerRef.current);
      revealTimerRef.current = null;
    }
  }

  const canAsk = chatConfigured !== false && Boolean(activeConversation);

  if (!active) return (<>
    <main className="library-shell">
      <header className="library-header"><Brand /><div className="library-actions"><SettingsButton onClick={() => setSettingsOpen(true)} /><UploadButton uploading={uploading} upload={upload} /></div></header>
      <section className="library-content">
        <p className="eyebrow">Your research desk</p>
        <h1>Read closely. Ask instantly.</h1>
        <p className="intro">Your papers and reading context stay on this machine. Select a passage and ask without breaking focus.</p>
        <label className="dropzone" onDragOver={(event) => event.preventDefault()} onDrop={(event) => { event.preventDefault(); const file = event.dataTransfer.files[0]; if (file) void upload(file); }}><span className="drop-icon">↥</span><strong>{uploading ? 'Processing your paper…' : 'Drop a research paper here'}</strong><span>or click to choose a PDF</span><input type="file" accept="application/pdf" hidden disabled={uploading} onChange={(event) => event.target.files?.[0] && void upload(event.target.files[0])} /></label>
        {error && <p className="error-banner">{error}</p>}
        {papers.length > 0 && <div className="paper-list"><div className="section-heading"><h2>Recent papers</h2><span>{papers.length}</span></div>{papers.map((paper) => <div className="paper-row" key={paper.id}><button className="paper-row-main" onClick={() => openPaper(paper)}><span className="paper-badge">PDF</span><span><strong>{paper.original_name}</strong><small>{paper.page_count} pages · stored locally</small></span><span className="row-arrow">→</span></button><button className="paper-delete" type="button" aria-label={`Delete ${paper.original_name}`} title="Delete paper" onClick={() => void deletePaper(paper)}><TrashIcon /></button></div>)}</div>}
      </section>
    </main>
    {settingsOpen && <SettingsDialog onClose={closeSettings} />}
  </>);

  return (
    <main className="reader-shell">
      <header className="reader-header"><button className="brand-button" onClick={() => { window.localStorage.removeItem('adam.activePaper'); setActive(null); }} aria-label="Back to library"><Brand /></button><div className="document-title">{renaming ? <form onSubmit={(event) => void renamePaper(event)}><input autoFocus aria-label="PDF filename" value={renameValue} maxLength={512} onChange={(event) => setRenameValue(event.target.value)} onKeyDown={(event) => { if (event.key === 'Escape') setRenaming(false); }} disabled={renameSaving} /><button type="submit" disabled={!renameValue.trim() || renameSaving}>{renameSaving ? 'Saving…' : 'Save'}</button></form> : <button type="button" className="document-title-button" title="Rename PDF" onClick={() => { setRenameValue(active.original_name.replace(/\.pdf$/i, '')); setRenaming(true); }}><strong>{active.original_name.replace(/\.pdf$/i, '')}</strong><span aria-hidden="true">✎</span></button>}<span>{pages || active.page_count} pages · local</span></div><div className="reader-header-tools"><div className="paper-zoom"><span className="control-label">Paper</span><div className="header-actions" role="group" aria-label="Paper zoom"><button title="Zoom paper out" aria-label="Zoom paper out" disabled={zoom <= MIN_ZOOM} onClick={() => changeZoom(-.1)}>−</button><span>{Math.round(zoom * 100)}%</span><button title="Zoom paper in" aria-label="Zoom paper in" disabled={zoom >= MAX_ZOOM} onClick={() => changeZoom(.1)}>+</button></div></div><button type="button" className="reader-delete-button" aria-label="Delete paper" title="Delete paper" onClick={() => void deletePaper(active)}><TrashIcon /></button><SettingsButton compact onClick={() => setSettingsOpen(true)} /></div></header>
      <div className="reader-workspace" ref={workspaceRef} style={{ gridTemplateColumns: `minmax(0, ${paperPercent}fr) minmax(340px, ${100 - paperPercent}fr)` }}>
        <section className={`pdf-pane${screenshotMode ? ' screenshot-mode' : ''}`} ref={viewerRef} onMouseUp={(event) => { if (!screenshotMode) captureSelection(event); }} onPointerDown={beginScreenshot} onPointerMove={moveScreenshot} onPointerUp={finishScreenshot} onPointerCancel={() => { screenshotPointerRef.current = null; setScreenshotDrag(null); setScreenshotMode(false); }}>
          <PdfDocument file={`${API_BASE}/api/documents/${active.id}/file`} onLoadSuccess={({ numPages }) => setPages(numPages)} loading={<div className="viewer-message">Rendering paper…</div>} error={<div className="viewer-message error-banner">Could not render this PDF.</div>}>
            {Array.from({ length: pages }, (_, index) => <div className="pdf-page-stage" data-page-number={index + 1} key={index + 1}><div className="pdf-page-wrap" style={{ zoom: zoom / MAX_ZOOM }}><PdfPageWithHighlights pageNumber={index + 1} highlights={highlightEntries.filter((entry) => entry.page === index + 1)} /><span className="page-label">{index + 1}</span></div></div>)}
          </PdfDocument>
          {screenshotMode && !screenshotDrag && <div className="screenshot-hint">Drag over the PDF to ask about it · Esc to cancel</div>}
          {screenshotDrag && <div className="screenshot-region" style={{ left: screenshotDrag.overlayLeft, top: screenshotDrag.overlayTop, width: screenshotDrag.overlayWidth, height: screenshotDrag.overlayHeight }} />}
        </section>
        <div className="pane-resizer" style={{ left: `${paperPercent}%` }} role="separator" aria-label="Resize paper and chat panes" aria-orientation="vertical" aria-valuemin={MIN_PAPER_PERCENT} aria-valuemax={80} aria-valuenow={Math.round(paperPercent)} tabIndex={0} onPointerDown={beginWorkspaceResize} onPointerMove={moveWorkspaceResize} onPointerUp={finishWorkspaceResize} onPointerCancel={() => { resizingRef.current = false; }} onDoubleClick={resetWorkspaceResize}><span /></div>
        <aside className="side-pane" style={{ '--chat-scale': chatScale } as CSSProperties}>
          <div className="mode-tabs"><div className="tab-list"><button className="active">Chat</button><button disabled>Notes <span>Soon</span></button></div><div className="chat-text-controls" role="group" aria-label="Chat text size"><span className="control-label">Text size</span><div><button type="button" aria-label="Decrease chat text size" title="Decrease chat text size" disabled={chatScale <= MIN_CHAT_SCALE} onClick={() => changeChatScale(-CHAT_SCALE_STEP)}>A−</button><output aria-live="polite" aria-label={`Chat text size ${Math.round(chatScale * 100)} percent`}>{Math.round(chatScale * 100)}%</output><button type="button" aria-label="Increase chat text size" title="Increase chat text size" disabled={chatScale >= MAX_CHAT_SCALE} onClick={() => changeChatScale(CHAT_SCALE_STEP)}>A+</button></div></div></div>
          {historyOpen ? <section className="history-view">
            <div className="history-header"><div><p>Conversations</p><h2>Chat history</h2><span>{conversations.length} saved for this paper</span></div><button type="button" onClick={() => setHistoryOpen(false)} aria-label="Close chat history">×</button></div>
            <button type="button" className="new-chat-card" onClick={() => void createNewConversation()}><span>＋</span><div><strong>Start a new chat</strong><small>Uses your current default model</small></div><i>→</i></button>
            <div className="history-list">{conversations.map((item) => <article className={`history-card${item.id === activeConversation?.id ? ' current' : ''}`} key={item.id}>{renamingChatId === item.id ? <form className="chat-title-form history-title-form" onSubmit={(event) => void renameConversation(event, item)}><input autoFocus value={chatTitleValue} maxLength={200} aria-label="Chat title" onChange={(event) => setChatTitleValue(event.target.value)} onKeyDown={(event) => { if (event.key === 'Escape') setRenamingChatId(null); }} /><button type="submit" disabled={!chatTitleValue.trim() || titleSaving}>Save</button><button type="button" onClick={() => setRenamingChatId(null)}>Cancel</button></form> : <button type="button" className="history-card-main" onClick={() => void openConversation(item)}><div className="history-card-top"><span className="history-model-mark">✦</span><time>{formatConversationDate(item.updated_at)}</time></div><strong>{item.title}</strong><p>{item.provider} · {item.model_id}</p><div className="history-card-meta"><span>{Math.ceil(item.message_count / 2)} {Math.ceil(item.message_count / 2) === 1 ? 'exchange' : 'exchanges'}</span><span>{item.context_builder_version === 'quick-ask-v1' ? 'Selection only' : 'Full paper'}</span>{item.id === activeConversation?.id && <em>Current</em>}</div></button>}<div className="history-card-actions"><button type="button" aria-label={`Rename ${item.title}`} title="Rename chat" onClick={() => beginChatRename(item)}>✎</button>{item.provider === 'zen' && item.message_count > 0 && <button type="button" aria-label={`Regenerate title for ${item.title}`} title="Regenerate title with GPT-5.6 Luna" disabled={titleSaving} onClick={() => void regenerateConversationTitle(item)}>↻</button>}<button type="button" className="history-delete" aria-label={`Delete ${item.title}`} title="Delete chat" onClick={() => void deleteConversation(item)}><TrashIcon /></button></div></article>)}</div>
          </section> : <>
            <div className="conversation-header"><div>{activeConversation && renamingChatId === activeConversation.id ? <form className="chat-title-form" onSubmit={(event) => void renameConversation(event, activeConversation)}><input autoFocus value={chatTitleValue} maxLength={200} aria-label="Chat title" onChange={(event) => setChatTitleValue(event.target.value)} onKeyDown={(event) => { if (event.key === 'Escape') setRenamingChatId(null); }} /><button type="submit" disabled={!chatTitleValue.trim() || titleSaving}>Save</button></form> : <button type="button" className="chat-title-button" title="Rename chat" onClick={() => activeConversation && beginChatRename(activeConversation)}><strong>{activeConversation?.title ?? 'Loading chat…'}</strong><span>✎</span></button>}<small>{activeConversation ? `${activeConversation.provider} · ${activeConversation.model_id}` : 'Preparing paper context'}</small></div><div className="conversation-actions">{activeConversation?.provider === 'zen' && activeConversation.message_count > 0 && <button type="button" disabled={titleSaving} onClick={() => void regenerateConversationTitle(activeConversation)} title="Regenerate title with GPT-5.6 Luna"><span>↻</span> Title</button>}<button type="button" onClick={() => void createNewConversation()} title="Start a new chat"><span>＋</span> New</button><button type="button" onClick={() => setHistoryOpen(true)}><span>☰</span> History</button></div></div>
            <div className="chat-body" ref={chatBodyRef} onWheelCapture={(event) => { if (event.deltaY < 0) pauseChatFollow(); }} onTouchMove={pauseChatFollow} onPointerDown={(event) => { if (event.target === event.currentTarget) pauseChatFollow(); }} onScroll={(event) => { const element = event.currentTarget; followOutputRef.current = element.scrollHeight - element.scrollTop - element.clientHeight < 40; }}><div className="chat-heading"><span className="spark">✦</span><div><strong>{activeConversation?.context_builder_version === 'quick-ask-v1' ? 'Saved Quick Ask' : 'Ask about this paper'}</strong><p>{activeConversation?.context_builder_version === 'quick-ask-v1' ? 'This saved thread uses only its original selection and Quick Ask prompt.' : 'The full paper is available automatically. Select text only when you want to focus the answer.'}</p></div></div>{chatConfigured === false && <button type="button" className="no-key-notice" onClick={() => setSettingsOpen(true)}><strong>No API key configured</strong><span>Choose a provider and add a key in Settings to enable chat.</span></button>}
              {chatHistory.map((turn) => <div className="chat-turn" key={turn.id}>{turn.importedQuickAsk && <div className="quick-import-badge">✦ Saved Quick Ask · selection only</div>}<ContextList selections={turn.context} /><div className="user-message"><span>You</span><p>{turn.question}</p></div><div className="answer-card complete"><div className="answer-meta"><span>Adam</span><span className="stream-state">Done</span></div><MarkdownAnswer>{turn.answer}</MarkdownAnswer></div></div>)}
              {submittedQuestion && <><ContextList selections={submittedContext} /><div className="user-message"><span>You</span><p>{submittedQuestion}</p></div></>}
              {streamStatus !== 'idle' && streamStatus !== 'error' && <div className={`answer-card ${streamStatus}`} aria-live="polite"><div className="answer-meta"><span>Adam</span><span className="stream-state">{streamStatus === 'connecting' ? <>Thinking<span className="thinking-dots"><i /><i /><i /></span></> : streamStatus === 'streaming' ? 'Responding…' : 'Done'}</span></div>{answer ? <MarkdownAnswer streaming={streamStatus === 'streaming'}>{answer}</MarkdownAnswer> : <div className="answer-skeleton"><i /><i /><i /></div>}</div>}{error && <p className="error-banner compact">{error}</p>}
              {contextSelections.length > 0 ? <ContextList selections={contextSelections} onRemove={(id) => setContextSelections((current) => current.filter((item) => item.id !== id))} /> : !submittedQuestion && chatHistory.length === 0 && <div className="empty-context"><span>✦</span><p>Ask anything about the paper, or select a passage for precise focus.</p></div>}
            </div>
            <form className="composer" onSubmit={ask}><textarea ref={composerRef} value={question} onChange={(event) => setQuestion(event.target.value)} onKeyDown={(event) => { if (event.key === 'Enter' && !event.shiftKey && !event.nativeEvent.isComposing) { event.preventDefault(); event.currentTarget.form?.requestSubmit(); } }} placeholder={chatConfigured === false ? 'Add an API key in Settings to chat…' : activeConversation?.context_builder_version === 'quick-ask-v1' ? 'Continue this saved Quick Ask…' : contextSelections.length ? 'Ask about your context…' : 'Ask anything about this paper…'} disabled={!canAsk || asking} rows={3} /><div><span>{chatConfigured === false ? 'Chat unavailable' : activeConversation?.context_builder_version === 'quick-ask-v1' ? 'Selection-only context' : contextSelections.length ? `${contextSelections.length} context ${contextSelections.length === 1 ? 'item' : 'items'}` : 'Full paper context'}</span><button type="submit" disabled={!canAsk || !question.trim() || asking}>{asking ? '…' : '↑'}</button></div></form>
          </>}
        </aside>
      </div>
      {pendingSelection && <div className="selection-toolbar" style={{ left: pendingSelection.x, top: pendingSelection.y }} onMouseDown={(event) => event.preventDefault()} role="toolbar" aria-label="Text selection actions"><div className="highlight-colors" aria-label="Highlight color">{HIGHLIGHT_COLORS.map(({ color, label, key }) => <button type="button" className="color-swatch" style={{ backgroundColor: color }} aria-label={`Highlight ${label.toLowerCase()} (${key})`} aria-keyshortcuts={key} title={`${label} highlight · ${key}`} onClick={() => applyHighlight(color)} key={color}><kbd>{key}</kbd></button>)}</div><span className="toolbar-divider" /><button type="button" className="toolbar-action primary" aria-keyshortcuts="C" onClick={addSelectionToContext}><span>＋</span>Add to context <kbd>C</kbd></button><button type="button" className="toolbar-action" aria-keyshortcuts="A" onClick={openQuickAskFromSelection}><span>✦</span>Ask AI <kbd>A</kbd></button><button type="button" className="toolbar-action" disabled title="Coming soon"><span>▱</span>Note <small>Beta</small></button></div>}
      {quickAskTarget && <form className="quick-ask-popover" style={{ left: quickAskTarget.x, top: quickAskTarget.y }} onSubmit={submitQuickAsk}><div className="quick-ask-head" onPointerDown={beginQuickDrag} onPointerMove={moveQuickDrag} onPointerUp={endQuickDrag} onPointerCancel={endQuickDrag}><span>⠿</span><span>✦ Quick Ask</span><small>{activeConversation?.model_id}</small><button type="button" aria-label="Close Quick Ask" onClick={closeQuickAsk}>×</button></div><div className={`quick-ask-context${quickAskTarget.imageDataUrl ? ' image' : ''}`}>{quickAskTarget.imageDataUrl ? <img src={quickAskTarget.imageDataUrl} alt="Selected PDF area" /> : <blockquote>{quickAskTarget.text}</blockquote>}</div><div className="quick-thread" ref={quickThreadRef} onWheelCapture={(event) => { if (event.deltaY < 0) quickFollowRef.current = false; }} onTouchMove={() => { quickFollowRef.current = false; }} onScroll={(event) => { const element = event.currentTarget; quickFollowRef.current = element.scrollHeight - element.scrollTop - element.clientHeight < 24; }}>{quickTurns.map((turn, index) => <div className="quick-turn" key={index}><div className="quick-user">{turn.question}</div><MarkdownAnswer>{turn.answer}</MarkdownAnswer></div>)}{(quickActiveQuestion && (quickAnswer || quickAsking)) && <div className="quick-turn"><div className="quick-user">{quickActiveQuestion}</div>{quickAnswer ? <MarkdownAnswer streaming={quickAsking}>{quickAnswer}</MarkdownAnswer> : <div className="answer-skeleton"><i /><i /><i /></div>}</div>}</div><div className="quick-ask-entry"><input ref={quickAskInputRef} value={quickQuestion} onChange={(event) => setQuickQuestion(event.target.value)} placeholder={quickTurns.length || quickAnswer ? 'Ask a follow-up…' : 'What would you like clarified?'} disabled={quickAsking} /><button type="submit" disabled={!quickQuestion.trim() || quickAsking}>{quickAsking ? '…' : '↑'}</button></div>{quickError && <p className="quick-ask-error">{quickError}</p>}<div className="quick-ask-footer"><span>Only this selection + this thread</span><div><button type="button" onClick={addQuickTargetToContext} disabled={quickAsking || quickImporting}>＋ Context <kbd>C</kbd></button><button type="button" className="move-to-chat" onClick={() => void importQuickAsk()} disabled={quickAsking || quickImporting || (!quickTurns.length && !quickAnswer)}>{quickImporting ? 'Saving…' : 'Save as new chat →'}</button></div></div></form>}
      {settingsOpen && <SettingsDialog onClose={closeSettings} />}
    </main>
  );
}

function formatContext(selections: ContextSelection[]) {
  return selections.filter((item) => !item.imageDataUrl).map((item, index) => `[Excerpt ${index + 1}${item.page ? `, page ${item.page}` : ''}]\n${item.text}`).join('\n\n');
}

function formatImages(selections: ContextSelection[]) {
  return selections.flatMap((item) => item.imageDataUrl ? [{ data_url: item.imageDataUrl, page: item.page }] : []);
}

function contextFromJson(value: string | null): ContextSelection[] {
  if (!value) return [];
  try {
    const context = JSON.parse(value) as { selected_text?: string; page?: number | null; images?: Array<{ data_url: string; page?: number | null }> };
    const items: ContextSelection[] = [];
    if (context.selected_text) items.push({ id: crypto.randomUUID(), text: context.selected_text, page: context.page ?? null });
    for (const image of context.images ?? []) items.push({ id: crypto.randomUUID(), text: 'Selected PDF area', page: image.page ?? null, imageDataUrl: image.data_url });
    return items;
  } catch { return []; }
}

function contextScope(value: string | null): string | null {
  try { return value ? (JSON.parse(value) as { scope?: string }).scope ?? null : null; } catch { return null; }
}

function formatConversationDate(value: string) {
  // SQLite returns UTC datetimes without an offset even when the SQLAlchemy
  // column is timezone-aware. Make that implicit UTC explicit before asking
  // the browser to format it in the user's local timezone.
  const date = new Date(/[zZ]$|[+-]\d{2}:?\d{2}$/.test(value) ? value : `${value}Z`);
  const today = new Date();
  if (date.toDateString() === today.toDateString()) return date.toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' });
  return date.toLocaleDateString([], { month: 'short', day: 'numeric' });
}

function TrashIcon() {
  return <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 7h16M9 7V4h6v3m3 0-1 13H7L6 7m4 4v5m4-5v5" /></svg>;
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
function SettingsButton({ onClick, compact = false }: { onClick: () => void; compact?: boolean }) {
  return <button type="button" className={`settings-button${compact ? ' compact-button' : ''}`} onClick={onClick} title="Settings (⌘/Ctrl + ,)" aria-label="Open settings" aria-keyshortcuts="Meta+, Control+,"><span className="settings-gear" aria-hidden="true">⚙</span><span>{compact ? '' : 'Settings'}</span></button>;
}

function SettingsDialog({ onClose }: { onClose: () => void }) {
  const [settings, setSettings] = useState<AppSettings | null>(null);
  const [provider, setProvider] = useState<ProviderId>('zen');
  const [model, setModel] = useState('');
  const [apiKey, setApiKey] = useState('');
  const [models, setModels] = useState<string[]>([]);
  const [query, setQuery] = useState('');
  const [status, setStatus] = useState('');
  const [saving, setSaving] = useState(false);
  const [loadingModels, setLoadingModels] = useState(false);
  const [systemPrompt, setSystemPrompt] = useState('');
  const [quickAskPrompt, setQuickAskPrompt] = useState('');
  const [promptKind, setPromptKind] = useState<'chat' | 'quick'>('chat');
  const [settingsTab, setSettingsTab] = useState<'providers' | 'system'>('providers');
  const dialogRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    void fetch(`${API_BASE}/api/settings`).then(async (response) => {
      if (!response.ok) throw new Error('Could not load settings.');
      const value = await response.json() as AppSettings;
      setSettings(value);
      setProvider(value.provider);
      setModel(value.model);
      setSystemPrompt(value.system_prompt);
      setQuickAskPrompt(value.quick_ask_prompt);
    }).catch((reason) => setStatus(reason instanceof Error ? reason.message : 'Could not load settings.'));
  }, []);

  useEffect(() => {
    const close = (event: KeyboardEvent) => { if (event.key === 'Escape') onClose(); };
    window.addEventListener('keydown', close);
    dialogRef.current?.focus();
    return () => window.removeEventListener('keydown', close);
  }, [onClose]);

  const loadModels = useCallback(async (providerId: ProviderId) => {
    setLoadingModels(true); setStatus('');
    try {
      const response = await fetch(`${API_BASE}/api/settings/models/${providerId}`);
      const value = await response.json();
      if (!response.ok) throw new Error(value.detail ?? 'Could not load models.');
      setModels(value.models);
    } catch (reason) { setModels([]); setStatus(reason instanceof Error ? reason.message : 'Could not load models.'); }
    finally { setLoadingModels(false); }
  }, []);

  async function persist() {
    if (!model.trim()) return;
    setSaving(true);
    setStatus('');
    try {
      const apiKeys = {};
      const response = await fetch(`${API_BASE}/api/settings`, { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ provider, model: model.trim(), api_keys: apiKeys, favorites: settings?.favorites ?? {} }) });
      const value = await response.json();
      if (!response.ok) throw new Error(value.detail ?? 'Could not save settings.');
      setSettings(value);
      setModel(value.selected_models[provider] ?? '');
      setApiKey('');
      setStatus(`${model.trim()} is now active`);
    } catch (reason) {
      setStatus(reason instanceof Error ? reason.message : 'Could not save settings.');
    } finally { setSaving(false); }
  }

  async function saveProviderKey(remove = false) {
    setSaving(true); setStatus('');
    try {
      const response = await fetch(`${API_BASE}/api/settings/key`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ provider, api_key: remove ? null : apiKey }) });
      const value = await response.json() as AppSettings;
      if (!response.ok) throw new Error((value as unknown as { detail?: string }).detail ?? 'Could not save API key.');
      setSettings(value); setApiKey(''); setStatus(remove ? 'API key removed' : 'API key connected');
      if (!remove) await loadModels(provider);
    } catch (reason) { setStatus(reason instanceof Error ? reason.message : 'Could not save API key.'); }
    finally { setSaving(false); }
  }

  async function saveSystemPrompt() {
    if (!settings || !systemPrompt.trim() || !quickAskPrompt.trim()) return;
    setSaving(true); setStatus('');
    try {
      const response = await fetch(`${API_BASE}/api/settings`, { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ provider: settings.provider, model: settings.model, api_keys: {}, favorites: settings.favorites, system_prompt: systemPrompt.trim(), quick_ask_prompt: quickAskPrompt.trim() }) });
      const value = await response.json() as AppSettings;
      if (!response.ok) throw new Error((value as unknown as { detail?: string }).detail ?? 'Could not save system prompt.');
      setSettings(value); setSystemPrompt(value.system_prompt); setQuickAskPrompt(value.quick_ask_prompt); setStatus('System prompt saved');
    } catch (reason) { setStatus(reason instanceof Error ? reason.message : 'Could not save system prompt.'); }
    finally { setSaving(false); }
  }

  function chooseProvider(next: ProviderId) { setProvider(next); setModel(settings?.selected_models[next] ?? ''); setModels([]); setQuery(''); setApiKey(''); if (settings?.providers[next]) void loadModels(next); }
  function toggleFavorite(id: string) {
    if (!settings) return;
    const current = settings.favorites[provider] ?? [];
    const starred = !current.includes(id);
    const next = starred ? [id, ...current] : current.filter((item) => item !== id);
    setSettings({ ...settings, favorites: { ...settings.favorites, [provider]: next } });
    void fetch(`${API_BASE}/api/settings/favorite`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ provider, model: id, starred }) }).then(async (response) => {
      if (!response.ok) throw new Error((await response.json()).detail ?? 'Could not save pinned model.');
    }).catch((reason) => { setSettings(settings); setStatus(reason instanceof Error ? reason.message : 'Could not save pinned model.'); });
  }
  const favorites = settings?.favorites[provider] ?? [];
  const visibleModels = models.filter((item) => item.toLowerCase().includes(query.toLowerCase()));
  const pinnedModels = favorites.filter((item) => visibleModels.includes(item));
  const otherModels = visibleModels.filter((item) => !favorites.includes(item));
  const providerInfo = PROVIDERS.find((item) => item.id === provider)!;
  const activeModel = settings?.selected_models[provider] ?? '';
  const hasPendingModel = Boolean(model && model !== activeModel);

  const modelRow = (id: string) => <button type="button" className={`${model === id ? 'selected' : ''}${activeModel === id ? ' active-model' : ''}`} onClick={() => { setModel(id); setStatus(id === activeModel ? '' : 'Selection not applied yet'); }} key={id}><span>{id}{activeModel === id && <small className="active-model-label">Active</small>}</span><i role="button" aria-label={favorites.includes(id) ? `Unpin ${id}` : `Pin ${id}`} title={favorites.includes(id) ? 'Unpin model' : 'Pin model'} onClick={(event) => { event.stopPropagation(); toggleFavorite(id); }}>{favorites.includes(id) ? '★' : '☆'}</i></button>;

  return <div className="settings-backdrop" onMouseDown={(event) => { if (event.target === event.currentTarget) onClose(); }}>
    <div className="settings-dialog provider-dialog" ref={dialogRef} role="dialog" aria-modal="true" aria-labelledby="settings-title" tabIndex={-1}>
      <div className="settings-titlebar"><div><span className="settings-kicker">Preferences</span><h2 id="settings-title">Settings</h2></div><button type="button" onClick={onClose} aria-label="Close settings">×</button></div>
      <div className="provider-layout">
        <nav className="provider-nav" aria-label="Settings sections">
          <span className="nav-section-label">Providers</span>
          {PROVIDERS.map((item) => <button type="button" className={settingsTab === 'providers' && provider === item.id ? 'active' : ''} onClick={() => { setSettingsTab('providers'); chooseProvider(item.id); }} key={item.id}><span className={`provider-dot${settings?.providers[item.id] ? ' connected' : ''}`} />{item.name}{settings?.providers[item.id] && <small>{settings.provider === item.id ? 'In use' : 'Ready'}</small>}</button>)}
          <span className="nav-section-label secondary-label">General</span>
          <button type="button" className={`more-settings-tab${settingsTab === 'system' ? ' active' : ''}`} onClick={() => { setSettingsTab('system'); setStatus(''); }}><span className="more-tab-icon">¶</span>System prompt</button>
        </nav>
        {settingsTab === 'providers' ? <div className="provider-content">
          <div className="provider-heading"><div><h3>{providerInfo.name}</h3><p>{settings?.providers[provider] ? 'API key configured' : 'Add a key to enable chat and load available models.'}</p></div>{settings?.providers[provider] && <button type="button" className="secondary-button" onClick={() => void loadModels(provider)} disabled={loadingModels}>{loadingModels ? 'Loading…' : 'Refresh models'}</button>}</div>
          <div className="key-row"><label className="field-label">{providerInfo.keyLabel}<input type="password" value={apiKey} onChange={(event) => setApiKey(event.target.value)} autoComplete="off" placeholder={settings?.providers[provider] ? '••••••••••••  Key configured' : 'Enter API key'} /></label><button type="button" className="secondary-button" disabled={!apiKey || saving} onClick={() => void saveProviderKey()}>{saving ? 'Saving…' : 'Save & connect'}</button></div>
          {settings?.providers[provider] && <button type="button" className="remove-key-button" onClick={() => void saveProviderKey(true)}>Remove this API key</button>}
          <div className="selected-model-summary"><span>{settings?.provider === provider ? 'Currently in use' : 'Saved model for this provider'}</span><strong>{activeModel || 'No model selected'}</strong><small>{settings?.provider === provider ? 'In use' : providerInfo.name}</small></div>
          {hasPendingModel && <div className="pending-model-choice"><span>Pending selection</span><strong>{model}</strong><small>Click “Apply model” to use it</small></div>}
          <div className="model-picker"><div className="model-picker-head"><label>Browse models</label><input type="search" value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Search models…" /></div>{visibleModels.length ? <div className="model-list">{pinnedModels.length > 0 && <div className="model-group-label"><span>★ Pinned</span><small>{pinnedModels.length}</small></div>}{pinnedModels.map(modelRow)}{otherModels.length > 0 && pinnedModels.length > 0 && <div className="model-group-label all-models"><span>All models</span><small>{otherModels.length}</small></div>}{otherModels.map(modelRow)}</div> : <div className="model-empty">{loadingModels ? 'Loading models…' : settings?.providers[provider] ? 'Refresh to load models from this provider.' : 'Connect an API key to browse models.'}</div>}</div>
        </div> : <div className="system-prompt-page"><span className="settings-kicker">Assistant behavior</span><h3>System prompts</h3><div className="prompt-kind-tabs"><button type="button" className={promptKind === 'chat' ? 'active' : ''} onClick={() => setPromptKind('chat')}>Chat</button><button type="button" className={promptKind === 'quick' ? 'active' : ''} onClick={() => setPromptKind('quick')}>Quick Ask</button></div><p>{promptKind === 'chat' ? 'Sent with every full chat. Controls how Adam reads paper context and writes answers.' : 'Sent only for inline questions. Quick Ask receives the selected excerpt or screenshot and nothing else.'}</p><label htmlFor="system-prompt-editor">{promptKind === 'chat' ? 'Chat prompt' : 'Quick Ask prompt'}</label><textarea id="system-prompt-editor" value={promptKind === 'chat' ? systemPrompt : quickAskPrompt} onChange={(event) => promptKind === 'chat' ? setSystemPrompt(event.target.value) : setQuickAskPrompt(event.target.value)} spellCheck rows={16} /></div>}
      </div>
      <div className="settings-footer"><span role="status" className={status.includes('active') || status.includes('saved') ? 'save-success' : ''}>{status}</span><div><kbd>Esc</kbd><button type="button" className="secondary-button" onClick={onClose}>Close</button>{settingsTab === 'providers' ? <button type="button" className="primary-button" disabled={!settings || !hasPendingModel || saving || !settings.providers[provider]} onClick={() => void persist()}>{saving ? 'Applying…' : 'Apply model'}</button> : <button type="button" className="primary-button" disabled={!settings || !systemPrompt.trim() || !quickAskPrompt.trim() || (systemPrompt.trim() === settings.system_prompt && quickAskPrompt.trim() === settings.quick_ask_prompt) || saving} onClick={() => void saveSystemPrompt()}>{saving ? 'Saving…' : 'Save prompt'}</button>}</div></div>
    </div>
  </div>;
}
function MarkdownAnswer({ children, streaming = false }: { children: string; streaming?: boolean }) {
  return <div className="markdown-answer"><ReactMarkdown remarkPlugins={[remarkGfm, remarkMath]} rehypePlugins={[rehypeKatex]} components={{ a: ({ children: linkText, ...props }) => <a {...props} target="_blank" rel="noreferrer">{linkText}</a> }}>{children}</ReactMarkdown>{streaming && <i className="stream-cursor" />}</div>;
}
function UploadButton({ uploading, upload }: { uploading: boolean; upload: (file: File) => Promise<void> }) { return <label className="primary-button">{uploading ? 'Opening…' : 'Open PDF'}<input type="file" accept="application/pdf" hidden disabled={uploading} onChange={(event) => event.target.files?.[0] && void upload(event.target.files[0])} /></label>; }

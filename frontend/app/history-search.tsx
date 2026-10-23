'use client';
import { Fragment, useEffect, useState } from 'react';
import { findMatches } from './find-text';

export function HistoryMatch({ text, query }: { text: string; query: string }) {
  const matches = findMatches(text, query);
  return <>{matches.map(({ start, end }, index) => <Fragment key={start}>{text.slice(index ? matches[index - 1].end : 0, start)}<mark className="history-search-match">{text.slice(start, end)}</mark></Fragment>)}{text.slice(matches.length ? matches[matches.length - 1].end : 0)}</>;
}

export default function HistorySearch({ documentId, onResults, onQuery, initialQuery = '' }: { documentId: string; initialQuery?: string; onQuery: (query: string) => void; onResults: (matches: Record<string, string> | null) => void }) {
  const [query, setQuery] = useState(initialQuery);
  const [status, setStatus] = useState('');
  useEffect(() => {
    onQuery(query.trim());
    const controller = new AbortController();
    if (!query.trim()) { onResults(null); return; }
    const timer = setTimeout(async () => {
      setStatus('Searching…');
      try {
        const response = await fetch(`/api/documents/${documentId}/conversation-search?q=${encodeURIComponent(query.trim())}`, { signal: controller.signal });
        if (!response.ok) throw new Error();
        const matches = await response.json() as { id: string; snippet: string }[];
        if (controller.signal.aborted) return;
        onResults(Object.fromEntries(matches.map((match) => [match.id, match.snippet])));
        setStatus(matches.length ? `${matches.length} ${matches.length === 1 ? 'chat' : 'chats'} found` : 'No chats found. Try another word.');
      } catch { if (!controller.signal.aborted) setStatus('Search unavailable. Try again.'); }
    }, 160);
    return () => { clearTimeout(timer); controller.abort(); };
  }, [query, documentId, onResults, onQuery]);
  return <div className="history-search" role="search" aria-label="Search chat history"><div><span aria-hidden="true">⌕</span><input aria-label="Search chat history" aria-keyshortcuts="Meta+Shift+f Control+Shift+f" title="Search chat history · Ctrl/Cmd+Shift+F" placeholder="Search titles and messages…" maxLength={200} value={query} onChange={(event) => { setQuery(event.target.value); setStatus(''); }} onKeyDown={(event) => { if (event.key === 'Escape') { event.stopPropagation(); setQuery(''); setStatus(''); } }} />{query && <button type="button" aria-label="Clear history search" onClick={() => { setQuery(''); setStatus(''); }}>×</button>}</div><small role="status">{query.trim() ? status : 'Search all chats for this paper'}</small></div>;
}

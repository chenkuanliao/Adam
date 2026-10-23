'use client';

import { useEffect, useRef, useState } from 'react';
import PaperDiscovery from './paper-discovery';
import { FolderIcon, folderStyle } from './folder-appearance';
import { Icon, type Folder, type LibraryPaper } from './library-dashboard';

type Result = { kind: 'folder'; folder: Folder } | { kind: 'paper'; paper: LibraryPaper };

export default function LibrarySpotlight({ papers, onClose, onOpen, onBrowseFolder, initialMode = 'library', initialFolderId = null, onImported }: {
  papers: LibraryPaper[];
  initialMode?: 'library' | 'online';
  initialFolderId?: string | null;
  onImported: (paper: LibraryPaper) => void;
  onClose: () => void;
  onOpen: (paper: LibraryPaper) => void;
  onBrowseFolder: (folderId: string) => void;
}) {
  const [mode, setMode] = useState<'library' | 'online' | 'link'>(initialMode);
  const [query, setQuery] = useState('');
  const [link, setLink] = useState('');
  const [selected, setSelected] = useState(0);
  const [folders, setFolders] = useState<Folder[]>([]);
  const [scope, setScope] = useState<Folder | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const dialogRef = useRef<HTMLDialogElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  const resultsRef = useRef<HTMLDivElement>(null);
  const term = query.trim().toLocaleLowerCase();
  const matchingFolders = folders.filter((folder) => (!scope || folder.parent_id === scope.id) && folder.name.toLocaleLowerCase().includes(term));
  const matchingPapers = papers.filter((paper) => (!scope || paper.folder_id === scope.id) && paper.original_name.toLocaleLowerCase().includes(term));
  const results: Result[] = [...matchingFolders.map((folder): Result => ({ kind: 'folder', folder })), ...matchingPapers.map((paper): Result => ({ kind: 'paper', paper }))];
  const activeIndex = Math.min(selected, Math.max(0, results.length - 1));
  const scopeCount = scope ? papers.filter((paper) => paper.folder_id === scope.id).length : 0;

  useEffect(() => {
    const dialog = dialogRef.current;
    dialog?.showModal();
    inputRef.current?.focus();
    let cancelled = false;
    void fetch('/api/folders').then(async (response) => {
      if (!response.ok) throw new Error('Could not load folders. You can still search your papers.');
      const payload = await response.json() as Folder[];
      if (!cancelled) setFolders(payload);
    }).catch((reason) => { if (!cancelled) setError(reason instanceof Error ? reason.message : 'Could not load folders.'); }).finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; dialog?.close(); };
  }, []);

  useEffect(() => { resultsRef.current?.querySelector('[data-active="true"]')?.scrollIntoView({ block: 'nearest' }); }, [activeIndex, scope, query]);

  function changeScope(folder: Folder | null) {
    setScope(folder); setQuery(''); setSelected(0); inputRef.current?.focus();
  }

  function openResult(result: Result) {
    if (result.kind === 'folder') changeScope(result.folder);
    else {
      if (scope) window.localStorage.setItem('adam.libraryLocation', scope.id);
      onOpen(result.paper);
    }
  }

  return <dialog ref={dialogRef} className={`paper-spotlight folder-spotlight${mode !== 'library' ? ' discovery-spotlight' : ''}`} aria-label="Search papers and folders" onCancel={onClose} onKeyDownCapture={(event) => {
    if ((event.metaKey || event.ctrlKey) && !event.altKey && event.key.toLowerCase() === 'k') {
      event.preventDefault();
      event.stopPropagation();
      setMode((current) => event.shiftKey ? 'online' : current === 'library' ? 'online' : current === 'online' ? 'link' : 'library');
      inputRef.current?.focus();
      return;
    }
    if (event.key === 'Escape') {
      // Search inputs otherwise consume Escape to clear their value first.
      event.preventDefault();
      event.stopPropagation();
      onClose();
    }
  }} onClick={(event) => {
    if (event.target === event.currentTarget) {
      const rect = event.currentTarget.getBoundingClientRect();
      if (event.clientX < rect.left || event.clientX > rect.right || event.clientY < rect.top || event.clientY > rect.bottom) onClose();
    }
  }} onKeyDown={(event) => {
    if (mode !== 'library') {
      if (event.key === 'Enter' && event.target === inputRef.current) { event.preventDefault(); (dialogRef.current?.querySelector('.spotlight-panel:not([hidden]) .discovery-search') as HTMLFormElement | null)?.requestSubmit(); }
      return;
    }
    if (event.target !== inputRef.current) return;
    if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
      event.preventDefault();
      setSelected(Math.max(0, Math.min(results.length - 1, activeIndex + (event.key === 'ArrowDown' ? 1 : -1))));
    } else if (event.key === 'Enter' && results[activeIndex]) {
      event.preventDefault(); openResult(results[activeIndex]);
    } else if (event.key === 'Backspace' && !query && scope && event.target === inputRef.current) {
      event.preventDefault(); changeScope(null);
    }
  }}>
    <div className="discovery-tabs" role="group" aria-label="Search mode"><button type="button" aria-pressed={mode === 'library'} onClick={() => { setMode('library'); inputRef.current?.focus(); }}>My library</button><button type="button" aria-pressed={mode === 'online'} aria-keyshortcuts="Meta+Shift+K Control+Shift+K" onClick={() => { setMode('online'); inputRef.current?.focus(); }}>Find papers online</button><button type="button" aria-pressed={mode === 'link'} onClick={() => { setMode('link'); inputRef.current?.focus(); }}>Add from link</button><span className="discovery-mode-hint"><kbd>⌘ / Ctrl K</kbd> Switch mode</span></div>
    <div className="spotlight-input"><Icon kind="search" /><input ref={inputRef} autoFocus type={mode === 'link' ? 'url' : 'search'} placeholder={mode === 'link' ? 'Paste a public PDF URL…' : mode === 'online' ? 'Paper title, keywords, DOI, or arXiv link…' : scope ? `Search in ${scope.name}…` : 'Search papers and folders…'} aria-label={mode === 'link' ? 'PDF URL' : mode === 'online' ? 'Search online papers' : scope ? `Search in ${scope.name}` : 'Search your papers and folders'} value={mode === 'link' ? link : query} onChange={(event) => { if (mode === 'link') setLink(event.target.value); else setQuery(event.target.value); setSelected(0); }} /><button className="spotlight-close" aria-label="Close search" data-tooltip="Close search (Escape)" onClick={onClose}><kbd>ESC</kbd></button></div>
    {error && <p className="error-banner" role="alert">{error}</p>}
    <div className="spotlight-panel" hidden={mode !== 'online'}><PaperDiscovery query={query} folders={folders} papers={papers} initialFolderId={scope?.id ?? initialFolderId} onImported={onImported} onOpen={onOpen} /></div>
    <div className="spotlight-panel" hidden={mode !== 'link'}><PaperDiscovery directLink query={link} folders={folders} papers={papers} initialFolderId={scope?.id ?? initialFolderId} onImported={onImported} onOpen={onOpen} /></div>
    <div className="spotlight-panel" hidden={mode !== 'library'}>
    {scope ? <div className="spotlight-scope"><button className="spotlight-back" data-tooltip="Return to all search results (Backspace in an empty search)" onClick={() => changeScope(null)}><span aria-hidden="true">←</span>All papers and folders</button><div className="spotlight-scope-heading" style={folderStyle(scope.color)}><span><FolderIcon value={scope.icon} /><strong>{scope.name}</strong><small>{scopeCount} {scopeCount === 1 ? 'paper' : 'papers'}</small></span><button data-tooltip={`View ${scope.name} in the dashboard`} onClick={() => onBrowseFolder(scope.id)}>Open folder <span aria-hidden="true">↗</span></button></div></div> : <div className="spotlight-search-hint">Search your entire library. Select a folder to explore its papers.{loading && <span role="status">Loading folders…</span>}</div>}
    <div className="spotlight-results" ref={resultsRef}>
      {results.map((result, index) => <button type="button" key={result.kind === 'folder' ? `folder-${result.folder.id}` : result.paper.id} className={index === activeIndex ? 'selected' : ''} data-active={index === activeIndex} aria-label={result.kind === 'folder' ? `Browse ${result.folder.name}` : `Open ${result.paper.original_name}`} data-tooltip={result.kind === 'folder' ? `Browse and search papers in ${result.folder.name}` : `Open ${result.paper.original_name}`} onMouseEnter={() => setSelected(index)} onClick={() => openResult(result)}>
        {result.kind === 'folder' ? <><span className="spotlight-folder-icon" style={folderStyle(result.folder.color)}><FolderIcon value={result.folder.icon} /></span><span><strong>{result.folder.name}</strong><small>Folder · {papers.filter((paper) => paper.folder_id === result.folder.id).length} papers</small></span><i aria-hidden="true">→</i></> : <><span className="spotlight-pdf">PDF</span><span><strong>{result.paper.original_name.replace(/\.pdf$/i, '')}</strong><small>{folders.find((folder) => folder.id === result.paper.folder_id)?.name ?? (result.paper.folder_id ? 'Folder' : 'Unfiled')} · {result.paper.page_count} pages · {Math.max(1, Math.round(result.paper.byte_size / 1024))} KB</small></span><i aria-hidden="true">↵</i></>}
      </button>)}
      {results.length === 0 && <div className="spotlight-empty"><strong>{scope && !scopeCount ? 'This folder is empty' : 'No matching papers or folders'}</strong><span>{scope && !scopeCount ? 'Open the folder to add a PDF or move papers into it.' : scope ? 'Try a different title or return to all papers and folders.' : 'Try searching with fewer words.'}</span>{query && <button className="files-secondary" onClick={() => { setQuery(''); setSelected(0); inputRef.current?.focus(); }}>Clear search</button>}</div>}
    </div>
    <footer><span><kbd>↑</kbd><kbd>↓</kbd> Navigate</span><span><kbd>↵</kbd> {results[activeIndex]?.kind === 'folder' ? 'Browse folder' : 'Open paper'}</span>{scope && <span><kbd>⌫</kbd> Back when search is empty</span>}<span><kbd>esc</kbd> Close</span></footer>
    </div>
  </dialog>;
}

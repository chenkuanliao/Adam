'use client';

import { useEffect, useRef, useState } from 'react';
import { folderStyle } from './folder-appearance';
import { Icon, type Folder, type LibraryPaper } from './library-dashboard';

type Result = { kind: 'folder'; folder: Folder } | { kind: 'paper'; paper: LibraryPaper };

export default function LibrarySpotlight({ papers, onClose, onOpen, onBrowseFolder }: {
  papers: LibraryPaper[];
  onClose: () => void;
  onOpen: (paper: LibraryPaper) => void;
  onBrowseFolder: (folderId: string) => void;
}) {
  const [query, setQuery] = useState('');
  const [selected, setSelected] = useState(0);
  const [folders, setFolders] = useState<Folder[]>([]);
  const [scope, setScope] = useState<Folder | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const dialogRef = useRef<HTMLDialogElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  const resultsRef = useRef<HTMLDivElement>(null);
  const term = query.trim().toLocaleLowerCase();
  const matchingFolders = scope ? [] : folders.filter((folder) => folder.name.toLocaleLowerCase().includes(term));
  const matchingPapers = papers.filter((paper) => (!scope || paper.folder_id === scope.id) && paper.original_name.toLocaleLowerCase().includes(term));
  const results: Result[] = [...matchingFolders.map((folder): Result => ({ kind: 'folder', folder })), ...matchingPapers.map((paper): Result => ({ kind: 'paper', paper }))];
  const activeIndex = Math.min(selected, Math.max(0, results.length - 1));
  const scopeCount = scope ? papers.filter((paper) => paper.folder_id === scope.id).length : 0;

  useEffect(() => {
    const dialog = dialogRef.current;
    dialog?.showModal();
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

  return <dialog ref={dialogRef} className="paper-spotlight folder-spotlight" aria-label="Search papers and folders" onCancel={onClose} onKeyDownCapture={(event) => {
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
    if ((event.target as HTMLElement).closest('.spotlight-scope, .spotlight-close')) return;
    if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
      event.preventDefault();
      setSelected(Math.max(0, Math.min(results.length - 1, activeIndex + (event.key === 'ArrowDown' ? 1 : -1))));
    } else if (event.key === 'Enter' && results[activeIndex]) {
      event.preventDefault(); openResult(results[activeIndex]);
    } else if (event.key === 'Backspace' && !query && scope && event.target === inputRef.current) {
      event.preventDefault(); changeScope(null);
    }
  }}>
    <div className="spotlight-input"><Icon kind="search" /><input ref={inputRef} autoFocus type="search" placeholder={scope ? `Search in ${scope.name}…` : 'Search papers and folders…'} aria-label={scope ? `Search in ${scope.name}` : 'Search your papers and folders'} value={query} onChange={(event) => { setQuery(event.target.value); setSelected(0); }} /><button className="spotlight-close" aria-label="Close search" data-tooltip="Close search (Escape)" onClick={onClose}><kbd>ESC</kbd></button></div>
    {scope ? <div className="spotlight-scope"><button className="spotlight-back" data-tooltip="Return to all search results (Backspace in an empty search)" onClick={() => changeScope(null)}><span aria-hidden="true">←</span>All papers and folders</button><div className="spotlight-scope-heading" style={folderStyle(scope.color)}><span><Icon /><strong>{scope.name}</strong><small>{scopeCount} {scopeCount === 1 ? 'paper' : 'papers'}</small></span><button data-tooltip={`View ${scope.name} in the dashboard`} onClick={() => onBrowseFolder(scope.id)}>Open folder <span aria-hidden="true">↗</span></button></div></div> : <div className="spotlight-search-hint">Search your entire library. Select a folder to explore its papers.{loading && <span role="status">Loading folders…</span>}</div>}
    {error && <p className="error-banner" role="alert">{error}</p>}
    <div className="spotlight-results" ref={resultsRef}>
      {results.map((result, index) => <button type="button" key={result.kind === 'folder' ? `folder-${result.folder.id}` : result.paper.id} className={index === activeIndex ? 'selected' : ''} data-active={index === activeIndex} aria-label={result.kind === 'folder' ? `Browse ${result.folder.name}` : `Open ${result.paper.original_name}`} data-tooltip={result.kind === 'folder' ? `Browse and search papers in ${result.folder.name}` : `Open ${result.paper.original_name}`} onMouseEnter={() => setSelected(index)} onClick={() => openResult(result)}>
        {result.kind === 'folder' ? <><span className="spotlight-folder-icon" style={folderStyle(result.folder.color)}><Icon /></span><span><strong>{result.folder.name}</strong><small>Folder · {papers.filter((paper) => paper.folder_id === result.folder.id).length} papers</small></span><i aria-hidden="true">→</i></> : <><span className="spotlight-pdf">PDF</span><span><strong>{result.paper.original_name.replace(/\.pdf$/i, '')}</strong><small>{folders.find((folder) => folder.id === result.paper.folder_id)?.name ?? (result.paper.folder_id ? 'Folder' : 'Unfiled')} · {result.paper.page_count} pages · {Math.max(1, Math.round(result.paper.byte_size / 1024))} KB</small></span><i aria-hidden="true">↵</i></>}
      </button>)}
      {results.length === 0 && <div className="spotlight-empty"><strong>{scope && !scopeCount ? 'This folder is empty' : 'No matching papers or folders'}</strong><span>{scope && !scopeCount ? 'Open the folder to add a PDF or move papers into it.' : scope ? 'Try a different title or return to all papers and folders.' : 'Try searching with fewer words.'}</span>{query && <button className="files-secondary" onClick={() => { setQuery(''); setSelected(0); inputRef.current?.focus(); }}>Clear search</button>}</div>}
    </div>
    <footer><span><kbd>↑</kbd><kbd>↓</kbd> Navigate</span><span><kbd>↵</kbd> {results[activeIndex]?.kind === 'folder' ? 'Browse folder' : 'Open paper'}</span>{scope && <span><kbd>⌫</kbd> Back when search is empty</span>}<span><kbd>esc</kbd> Close</span></footer>
  </dialog>;
}

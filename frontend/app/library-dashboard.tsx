'use client';

import FolderColorPicker, { DEFAULT_FOLDER_COLOR, folderStyle } from './folder-appearance';
import { useEffect, useRef, useState, type DragEvent, type ReactNode } from 'react';

export type LibraryPaper = { id: string; folder_id: string | null; original_name: string; byte_size: number; page_count: number; status: string; created_at: string; updated_at: string };
export type Folder = { id: string; name: string; color: string; created_at: string };
const PAPER_DRAG_TYPE = 'application/x-adam-papers';

type Modal = { kind: 'create' } | { kind: 'rename'; folder: Folder } | { kind: 'delete'; folder: Folder } | { kind: 'move'; ids: string[] };

export function Icon({ kind = 'folder' }: { kind?: 'folder' | 'grid' | 'list' | 'search' | 'file' | 'trash' }) {
  return <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">{kind === 'trash' ? <><path d="M3 6h18M9 6V3h6v3M5 6l1 15h12l1-15M10 10v7M14 10v7" /></> : kind === 'folder' ? <path d="M3 7V5a2 2 0 0 1 2-2h5l2 3h7a2 2 0 0 1 2 2v11a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V7Z" /> : kind === 'grid' ? <><rect x="3" y="3" width="7" height="7" rx="1" /><rect x="14" y="3" width="7" height="7" rx="1" /><rect x="3" y="14" width="7" height="7" rx="1" /><rect x="14" y="14" width="7" height="7" rx="1" /></> : kind === 'list' ? <><path d="M9 5h12M9 12h12M9 19h12M3 5h1M3 12h1M3 19h1" /></> : kind === 'search' ? <><circle cx="10.5" cy="10.5" r="6.5" /><path d="m16 16 5 5" /></> : <><path d="M14 3H5v18h14V8l-5-5Z" /><path d="M14 3v5h5M8 13h8M8 17h6" /></>}</svg>;
}

async function request<T>(path: string, method = 'GET', body?: unknown): Promise<T> {
  const response = await fetch(`/api/${path}`, { method, ...(body === undefined ? {} : { headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) }) });
  const payload = response.status === 204 ? null : await response.json() as { detail?: unknown };
  if (!response.ok) throw new Error(typeof payload?.detail === 'string' ? payload.detail : 'Could not save your changes. Please try again.');
  return payload as T;
}

export default function LibraryDashboard({ papers, loading, uploading, error, brand, settings, preview, onOpen, onUpload, onDelete, onUpdate, onSearch }: {
  papers: LibraryPaper[]; loading: boolean; uploading: boolean; error: string; brand: ReactNode; settings: ReactNode;
  preview: (paper: LibraryPaper) => ReactNode; onOpen: (paper: LibraryPaper) => void;
  onUpload: (file: File, folderId?: string | null) => Promise<void>; onDelete: (paper: LibraryPaper) => Promise<void>; onUpdate: (papers: LibraryPaper[]) => void; onSearch: () => void;
}) {
  const [folders, setFolders] = useState<Folder[]>([]);
  const [folderLoading, setFolderLoading] = useState(true);
  const [location, setLocation] = useState('all');
  const [query, setQuery] = useState('');
  const [sort, setSort] = useState('modified');
  const [view, setView] = useState<'grid' | 'list'>('grid');
  const [selected, setSelected] = useState<string[]>([]);
  const [modal, setModal] = useState<Modal | null>(null);
  const [name, setName] = useState('');
  const [folderColor, setFolderColor] = useState(DEFAULT_FOLDER_COLOR);
  const [destination, setDestination] = useState('');
  const [saving, setSaving] = useState(false);
  const [localError, setLocalError] = useState('');
  const [notice, setNotice] = useState('');
  const [dragOver, setDragOver] = useState(false);
  const [draggedIds, setDraggedIds] = useState<string[]>([]);
  const [dropTarget, setDropTarget] = useState<string | null>(null);
  const [movingIds, setMovingIds] = useState<string[]>([]);
  const dragPreviewRef = useRef<HTMLSpanElement>(null);
  const dragCountRef = useRef<HTMLSpanElement>(null);
  const dragIdsRef = useRef<string[]>([]);
  const movePendingRef = useRef(false);
  const suppressOpenUntil = useRef(0);
  const uploadRef = useRef<HTMLInputElement>(null);
  const dialogRef = useRef<HTMLDialogElement>(null);
  const currentFolder = folders.find((folder) => folder.id === location);
  const folderId = currentFolder?.id ?? null;
  const title = currentFolder?.name ?? (location === 'unfiled' ? 'Unfiled papers' : 'Paper library');
  const scoped = papers.filter((paper) => location === 'all' || (location === 'unfiled' ? !paper.folder_id : paper.folder_id === location));
  const visible = scoped.filter((paper) => paper.original_name.toLocaleLowerCase().includes(query.trim().toLocaleLowerCase())).toSorted((a, b) => sort === 'title' ? a.original_name.localeCompare(b.original_name) : new Date(sort === 'created' ? b.created_at : b.updated_at).getTime() - new Date(sort === 'created' ? a.created_at : a.updated_at).getTime());
  const visibleFolders = location === 'all' ? folders.filter((folder) => folder.name.toLocaleLowerCase().includes(query.trim().toLocaleLowerCase())) : [];
  const selectedIds = selected.filter((id) => papers.some((paper) => paper.id === id));

  useEffect(() => {
    let cancelled = false;
    request<Folder[]>('folders').then((result) => { if (!cancelled) {
      setFolders(result);
      const savedLocation = window.localStorage.getItem('adam.libraryLocation');
      if (savedLocation && (savedLocation === 'unfiled' || result.some((folder) => folder.id === savedLocation))) setLocation(savedLocation);
    } }).catch((reason) => { if (!cancelled) setLocalError(reason.message); }).finally(() => { if (!cancelled) { setFolderLoading(false); const stored = window.localStorage.getItem('adam.libraryView'); if (stored === 'list' || stored === 'grid') setView(stored); } });
    return () => { cancelled = true; };
  }, []);

  useEffect(() => { if (modal) dialogRef.current?.showModal(); }, [modal]);

  function navigate(next: string) { window.localStorage.setItem('adam.libraryLocation', next); setLocation(next); setQuery(''); setSelected([]); setLocalError(''); }
  function openModal(next: Modal) {
    if (movePendingRef.current) return;
    setLocalError('');
    setName(next.kind === 'rename' ? next.folder.name : '');
    setFolderColor(next.kind === 'rename' ? next.folder.color ?? DEFAULT_FOLDER_COLOR : DEFAULT_FOLDER_COLOR);
    const sources = next.kind === 'move' ? [...new Set(papers.filter((paper) => next.ids.includes(paper.id)).map((paper) => paper.folder_id ?? ''))] : [];
    setDestination(sources.length === 1 ? sources[0] : '__choose__');
    setModal(next);
  }
  function toggle(id: string) { setSelected((ids) => ids.includes(id) ? ids.filter((item) => item !== id) : [...ids, id]); }
  function chooseView(next: 'grid' | 'list') { setView(next); window.localStorage.setItem('adam.libraryView', next); }

  function endPaperDrag(event: DragEvent<HTMLElement>) {
    dragIdsRef.current = [];
    setDraggedIds([]);
    setDropTarget(null);
    suppressOpenUntil.current = event.timeStamp + 250;
  }

  function startPaperDrag(event: DragEvent<HTMLElement>, paper: LibraryPaper) {
    if (saving || movePendingRef.current) { event.preventDefault(); return; }
    const ids = selectedIds.includes(paper.id) ? selectedIds : [paper.id];
    if (ids.length > 500) {
      event.preventDefault();
      setLocalError('Select up to 500 papers at a time.');
      return;
    }
    event.dataTransfer.setData(PAPER_DRAG_TYPE, JSON.stringify(ids));
    event.dataTransfer.effectAllowed = 'move';
    if (dragPreviewRef.current && dragCountRef.current) {
      dragCountRef.current.textContent = String(ids.length);
      dragCountRef.current.hidden = ids.length === 1;
      event.dataTransfer.setDragImage(dragPreviewRef.current, 22, 22);
    }
    dragIdsRef.current = ids;
    setDraggedIds(ids);
    setLocalError('');
    setNotice('');
  }

  function canDropPapers(event: DragEvent<HTMLElement>, target: string | null) {
    return !saving && !movePendingRef.current && event.dataTransfer.types.includes(PAPER_DRAG_TYPE)
      && papers.some((paper) => dragIdsRef.current.includes(paper.id) && paper.folder_id !== target);
  }

  async function dropPapers(event: DragEvent<HTMLElement>, target: string | null) {
    if (!event.dataTransfer.types.includes(PAPER_DRAG_TYPE)) return;
    event.preventDefault();
    event.stopPropagation();
    const ids = canDropPapers(event, target)
      ? papers.filter((paper) => dragIdsRef.current.includes(paper.id) && paper.folder_id !== target).map((paper) => paper.id) : [];
    endPaperDrag(event);
    if (!ids.length) return;
    movePendingRef.current = true;
    setMovingIds(ids);
    setLocalError('');
    try {
      const updated = await request<LibraryPaper[]>('document-moves', 'POST', { document_ids: ids, folder_id: target });
      onUpdate(updated);
      setSelected((items) => items.filter((id) => !ids.includes(id)));
      setNotice(`Moved ${updated.length} ${updated.length === 1 ? 'paper' : 'papers'} to ${folders.find((folder) => folder.id === target)?.name ?? 'Unfiled'}.`);
    } catch (reason) {
      setLocalError(reason instanceof Error ? reason.message : 'Could not move papers. Please try again.');
    } finally {
      movePendingRef.current = false;
      setMovingIds([]);
    }
  }

  function handlePaperDragOver(event: DragEvent<HTMLElement>, target: string | null, surface: string) {
    if (!event.dataTransfer.types.includes(PAPER_DRAG_TYPE)) return;
    event.stopPropagation();
    const allowed = canDropPapers(event, target);
    event.dataTransfer.dropEffect = allowed ? 'move' : 'none';
    if (allowed) { event.preventDefault(); setDropTarget(surface); }
    else setDropTarget(null);
  }

  function handlePaperDragLeave(event: DragEvent<HTMLElement>, surface: string) {
    if (!(event.relatedTarget instanceof Node) || !event.currentTarget.contains(event.relatedTarget)) {
      setDropTarget((current) => current === surface ? null : current);
    }
  }

  async function saveModal() {
    if (!modal || saving || movePendingRef.current) return;
    setSaving(true); setLocalError('');
    try {
      if (modal.kind === 'move') {
        const updated = await request<LibraryPaper[]>('document-moves', 'POST', { document_ids: modal.ids, folder_id: destination || null });
        onUpdate(updated); setSelected([]); setNotice(`Moved ${updated.length} ${updated.length === 1 ? 'paper' : 'papers'} to ${folders.find((folder) => folder.id === destination)?.name ?? 'Unfiled'}.`);
      } else if (modal.kind === 'delete') {
        await request(`folders/${modal.folder.id}`, 'DELETE');
        setFolders((items) => items.filter((folder) => folder.id !== modal.folder.id));
        onUpdate(papers.filter((paper) => paper.folder_id === modal.folder.id).map((paper) => ({ ...paper, folder_id: null })));
        if (location === modal.folder.id) navigate('unfiled');
        setNotice('Folder removed. Its papers are in Unfiled.');
      } else {
        const folder = await request<Folder>(modal.kind === 'create' ? 'folders' : `folders/${modal.folder.id}`, modal.kind === 'create' ? 'POST' : 'PATCH', { name: name.trim(), color: folderColor });
        setFolders((items) => [...items.filter((item) => item.id !== folder.id), folder].toSorted((a, b) => a.name.localeCompare(b.name)));
        setNotice(modal.kind === 'create' ? `Created “${folder.name}”.` : 'Folder updated.');
      }
      setModal(null);
    } catch (reason) { setLocalError(reason instanceof Error ? reason.message : 'Could not save changes.'); }
    finally { setSaving(false); }
  }

  const movingPapers = modal?.kind === 'move' ? papers.filter((paper) => modal.ids.includes(paper.id)) : [];
  const sourceFolders = [...new Set(movingPapers.map((paper) => paper.folder_id ?? ''))];
  const currentMoveFolder = sourceFolders.length === 1 ? folders.find((folder) => folder.id === sourceFolders[0])?.name ?? 'Unfiled (no folder)' : 'Multiple folders';
  const moveUnchanged = modal?.kind === 'move' && movingPapers.every((paper) => (paper.folder_id ?? '') === destination);

  const addPDF = <button className="primary-button" data-tooltip={currentFolder ? `Add a PDF to ${currentFolder.name}` : 'Add a PDF to your library'} disabled={uploading || loading} onClick={() => uploadRef.current?.click()}><span aria-hidden="true">＋</span> {uploading ? 'Processing PDF…' : 'Add PDF'}</button>;

  return <main className="library-shell files-shell">
    <span ref={dragPreviewRef} className="files-drag-preview" aria-hidden="true"><Icon kind="file" /><span ref={dragCountRef} className="files-drag-count" hidden /></span>
    <header className="library-header">{brand}<span className="files-header-label">Your research, in one place</span><div className="library-actions">{settings}{addPDF}</div></header>
    <input ref={uploadRef} aria-label="Upload PDF" type="file" accept="application/pdf,.pdf" hidden disabled={uploading} onChange={(event) => { const file = event.target.files?.[0]; if (file) void onUpload(file, folderId); event.target.value = ''; }} />
    <div className="files-workspace">
      <aside className="files-sidebar"><p className="files-nav-label">WORKSPACE</p><nav aria-label="Library navigation">
        <button data-tooltip="View every paper in your library" className={location === 'all' ? 'active' : ''} onClick={() => navigate('all')}><Icon kind="grid" />All papers<span>{papers.length}</span></button>
        <button data-tooltip="View papers outside folders" onDragOver={(event) => handlePaperDragOver(event, null, 'unfiled')} onDragLeave={(event) => handlePaperDragLeave(event, 'unfiled')} onDrop={(event) => { void dropPapers(event, null); }} className={`${location === 'unfiled' ? 'active' : ''}${dropTarget === 'unfiled' ? ' files-folder-drop-target' : ''}`} onClick={() => navigate('unfiled')}><Icon kind="file" />Unfiled<span>{papers.filter((paper) => !paper.folder_id).length}</span></button>
        <div className="files-folder-heading"><p className="files-nav-label">FOLDERS</p><button aria-label="Create folder" data-tooltip="Create a folder to organize your papers" onClick={() => openModal({ kind: 'create' })}>＋</button></div>
        {folderLoading ? <p className="files-sidebar-hint">Loading folders…</p> : folders.map((folder) => <button key={folder.id} onDragOver={(event) => handlePaperDragOver(event, folder.id, `sidebar-${folder.id}`)} onDragLeave={(event) => handlePaperDragLeave(event, `sidebar-${folder.id}`)} onDrop={(event) => { void dropPapers(event, folder.id); }} style={folderStyle(folder.color)} data-tooltip={`View papers in ${folder.name}`} className={`files-colored-folder${location === folder.id ? ' active' : ''}${dropTarget === `sidebar-${folder.id}` ? ' files-folder-drop-target' : ''}`} onClick={() => navigate(folder.id)}><Icon /><span className="files-folder-name">{folder.name}</span><span>{papers.filter((paper) => paper.folder_id === folder.id).length}</span></button>)}
        {!folderLoading && !folders.length && <p className="files-sidebar-hint">Make a little room for your next big idea.</p>}
        <button className="files-new-folder" data-tooltip="Create a folder to organize your papers" onClick={() => openModal({ kind: 'create' })}><span aria-hidden="true">＋</span> New folder</button>
      </nav><div className="files-local-note"><span className="files-local-dot" />Stored locally<p>Your PDFs, notes, and chats stay on this machine.</p></div></aside>
      <section className={`files-content${dragOver ? ' files-drop-active' : ''}`} onDragOver={(event) => { if (event.dataTransfer.types.includes('Files')) { event.preventDefault(); setDragOver(true); } }} onDragLeave={(event) => { if (!event.currentTarget.contains(event.relatedTarget as Node)) setDragOver(false); }} onDrop={(event) => { event.preventDefault(); setDragOver(false); if (uploading) return; const file = event.dataTransfer.files[0]; if (file) void onUpload(file, folderId); }}>
        <div className="files-breadcrumb"><button onClick={() => navigate('all')}>Workspace</button><span>/</span><span>{location === 'all' ? 'All papers' : title}</span></div>
        <div className="files-heading"><div><p className="eyebrow">YOUR RESEARCH DESK</p><h1>{currentFolder && <span className="files-heading-folder-icon" style={folderStyle(currentFolder.color)}><Icon /></span>}{title}</h1><p>{currentFolder ? 'A little structure for your next discovery.' : location === 'unfiled' ? 'A home for papers you haven’t organized yet.' : 'Read, collect, and connect your ideas.'}</p></div><div className="files-heading-actions">{currentFolder ? <><button className="files-secondary" data-tooltip={currentFolder ? 'Edit the folder name and color' : 'Create a folder to organize your papers'} onClick={() => openModal({ kind: 'rename', folder: currentFolder })}>Edit folder</button><button className="files-icon-button" aria-label="Delete folder" data-tooltip="Remove this folder and keep its papers" onClick={() => openModal({ kind: 'delete', folder: currentFolder })}><Icon kind="trash" /></button></> : <button className="files-secondary" data-tooltip={currentFolder ? 'Edit the folder name and color' : 'Create a folder to organize your papers'} onClick={() => openModal({ kind: 'create' })}><Icon />New folder</button>}</div></div>
        {(error || (localError && !modal)) && <p className="error-banner" role="alert">{error || localError}</p>}
        {movingIds.length > 0 && <div className="files-notice" role="status">Moving {movingIds.length === 1 ? 'paper' : `${movingIds.length} papers`}…</div>}
        {notice && <div className="files-notice" role="status">{notice}<button data-tooltip="Dismiss this message" aria-label="Dismiss notification" onClick={() => setNotice('')}>×</button></div>}
        <div className="files-toolbar"><button className="files-quick-search" aria-label="Search all papers and folders" data-tooltip="Search all papers and folders (⌘ / Ctrl + K)" onClick={onSearch}><Icon kind="search" /><kbd>⌘ K</kbd></button><div className="files-search"><Icon kind="search" /><input aria-label="Search papers and folders" type="search" placeholder={currentFolder ? `Search in ${currentFolder.name}…` : 'Search papers and folders…'} value={query} onChange={(event) => setQuery(event.target.value)} /></div><select data-tooltip="Sort papers by title or date" aria-label="Sort papers" value={sort} onChange={(event) => setSort(event.target.value)}><option value="modified">Last modified</option><option value="created">Date added</option><option value="title">Title A–Z</option></select><div className="files-view-switch" role="group" aria-label="View layout"><button data-tooltip="Show paper previews in a grid" aria-label="Grid view" aria-pressed={view === 'grid'} onClick={() => chooseView('grid')}><Icon kind="grid" /></button><button data-tooltip="Show papers in a compact list" aria-label="List view" aria-pressed={view === 'list'} onClick={() => chooseView('list')}><Icon kind="list" /></button></div></div>
        {visibleFolders.length > 0 && <><div className="files-section-heading"><h2>Folders</h2><span>{visibleFolders.length}</span></div><div className="files-folder-grid">{visibleFolders.map((folder) => <button onDragOver={(event) => handlePaperDragOver(event, folder.id, `card-${folder.id}`)} onDragLeave={(event) => handlePaperDragLeave(event, `card-${folder.id}`)} onDrop={(event) => { void dropPapers(event, folder.id); }} className={`files-folder-card${dropTarget === `card-${folder.id}` ? ' files-folder-drop-target' : ''}`} style={folderStyle(folder.color)} data-tooltip={`View papers in ${folder.name}`} key={folder.id} onClick={() => navigate(folder.id)}><span className="files-folder-icon"><Icon /></span><strong>{folder.name}</strong><small>{dropTarget === `card-${folder.id}` ? 'Drop to move here' : `${papers.filter((paper) => paper.folder_id === folder.id).length} papers`}</small><span className="files-folder-arrow" aria-hidden="true">↗</span></button>)}</div></>}
        <div className="files-section-heading files-paper-heading"><h2>{location === 'all' ? 'All papers' : 'Papers'}</h2><span>{visible.length}</span><div className="files-selection-actions">{visible.length > 0 && <label><input type="checkbox" aria-label="Select all visible papers" checked={visible.every((paper) => selectedIds.includes(paper.id))} onChange={(event) => setSelected(event.target.checked ? [...new Set([...selectedIds, ...visible.map((paper) => paper.id)])] : selectedIds.filter((id) => !visible.some((paper) => paper.id === id)))} />Select all</label>}</div></div>
        {selectedIds.length > 0 && <div className="files-selection-bar"><strong>{selectedIds.length} selected</strong><button data-tooltip="Move selected papers into a folder" disabled={selectedIds.length > 500 || movingIds.length > 0} onClick={() => openModal({ kind: 'move', ids: selectedIds })}><Icon />Move to folder</button><button data-tooltip="Deselect all papers" onClick={() => setSelected([])}>Clear selection</button>{selectedIds.length > 500 && <small>Select up to 500 papers at a time.</small>}</div>}
        {loading ? <div className="files-empty" role="status"><Icon kind="file" /><h2>Loading your library…</h2></div> : visible.length === 0 ? <div className="files-empty"><span className="files-empty-icon"><Icon kind={query ? 'search' : currentFolder ? 'folder' : 'file'} /></span><h2>{query ? 'No matching papers' : currentFolder ? 'Room for new ideas' : papers.length ? 'Everything has a place' : 'Your next idea starts here'}</h2><p>{query ? 'Try a different title or explore another folder.' : currentFolder ? 'Add a PDF here, or select papers in All papers and move them into this folder.' : papers.length ? 'Papers outside folders will appear here.' : 'Add your first PDF. Keep it here or organize it into a folder.'}</p>{query ? <button className="files-secondary" onClick={() => setQuery('')}>Clear search</button> : addPDF}<small>Drop a PDF here to add it. Drag papers onto a folder to organize them.</small></div> : <div className={`files-papers files-${view}`}>
          {visible.map((paper) => <article className={`files-paper${selectedIds.includes(paper.id) ? ' is-selected' : ''}${draggedIds.includes(paper.id) ? ' is-dragging' : ''}${movingIds.includes(paper.id) ? ' is-moving' : ''}`} aria-busy={movingIds.includes(paper.id)} key={paper.id}><label className="files-paper-select"><input type="checkbox" aria-label={`Select ${paper.original_name}`} checked={selectedIds.includes(paper.id)} onChange={() => toggle(paper.id)} /></label><button className="files-paper-open" draggable={!saving && movingIds.length === 0} onDragStart={(event) => startPaperDrag(event, paper)} onDragEnd={endPaperDrag} onClick={(event) => { if (event.timeStamp >= suppressOpenUntil.current) onOpen(paper); }} data-tooltip={`Open ${paper.original_name}`} aria-label={`Open ${paper.original_name}`}>{view === 'grid' ? preview(paper) : <span className="files-list-icon"><Icon kind="file" /></span>}<span className="files-paper-body"><span className="files-paper-type">PDF<span>{folders.find((folder) => folder.id === paper.folder_id)?.name ?? 'Unfiled'}</span></span><strong>{paper.original_name.replace(/\.pdf$/i, '')}</strong><span className="files-paper-meta">{paper.page_count} {paper.page_count === 1 ? 'page' : 'pages'}<span>·</span>{paper.byte_size < 1048576 ? `${Math.round(paper.byte_size / 1024)} KB` : `${(paper.byte_size / 1048576).toFixed(1)} MB`}</span><span className="files-paper-date">Modified {new Date(paper.updated_at).toLocaleDateString(undefined, { month: 'short', day: 'numeric', year: 'numeric' })}</span></span></button><div className="files-paper-actions"><button aria-label={`Move ${paper.original_name}`} data-tooltip={`Move paper · currently in ${folders.find((folder) => folder.id === paper.folder_id)?.name ?? 'Unfiled'}`} disabled={movingIds.length > 0} onClick={() => openModal({ kind: 'move', ids: [paper.id] })}><Icon /></button><button aria-label={`Delete ${paper.original_name}`} data-tooltip="Permanently delete this paper and its notes and chats" disabled={movingIds.includes(paper.id)} onClick={() => void onDelete(paper)}><Icon kind="trash" /></button></div></article>)}
        </div>}
        {visible.length > 0 && <button className="files-add-more" data-tooltip={currentFolder ? `Add a PDF to ${currentFolder.name}` : 'Add a PDF to your library'} disabled={uploading} onClick={() => uploadRef.current?.click()}><span aria-hidden="true">＋</span>{uploading ? 'Processing paper…' : 'Add a paper to this collection'}<small>or drop a PDF here</small></button>}
      </section>
    </div>
    {modal && <dialog ref={dialogRef} className="files-dialog" aria-labelledby="folder-dialog-title" onCancel={(event) => { if (saving) event.preventDefault(); else setModal(null); }} onClick={(event) => { if (event.target === event.currentTarget && !saving) { const rect = event.currentTarget.getBoundingClientRect(); if (event.clientX < rect.left || event.clientX > rect.right || event.clientY < rect.top || event.clientY > rect.bottom) setModal(null); } }}><form onSubmit={(event) => { event.preventDefault(); void saveModal(); }}><span className="files-dialog-icon" style={folderStyle(modal.kind === 'create' || modal.kind === 'rename' ? folderColor : modal.kind === 'delete' ? modal.folder.color : folders.find((folder) => folder.id === destination)?.color)}><Icon /></span><h2 id="folder-dialog-title">{modal.kind === 'create' ? 'A home for your ideas' : modal.kind === 'rename' ? 'Edit folder' : modal.kind === 'delete' ? 'Remove this folder?' : 'Move papers'}</h2><p>{modal.kind === 'create' ? 'Group papers by project, topic, or whatever works for you.' : modal.kind === 'rename' ? 'Choose a name and color that make this collection easy to find.' : modal.kind === 'delete' ? `“${modal.folder.name}” will be removed. All its papers, notes, and chats will be kept. Papers will return to Unfiled.` : `Choose a home for ${modal.ids.length} ${modal.ids.length === 1 ? 'paper' : 'papers'}.`}</p>{modal.kind === 'create' || modal.kind === 'rename' ? <><label>Folder name<input autoFocus value={name} maxLength={80} placeholder="e.g. Machine learning" onChange={(event) => setName(event.target.value)} disabled={saving} required /></label><FolderColorPicker value={folderColor} onChange={setFolderColor} disabled={saving} /></> : modal.kind === 'move' ? <><div className="files-move-current"><span>Current folder</span><strong>{currentMoveFolder}</strong></div><label>Destination<select autoFocus value={destination} onChange={(event) => setDestination(event.target.value)} disabled={saving}><option value="__choose__" disabled>Choose a destination…</option><option value="">Unfiled (no folder){sourceFolders.length === 1 && sourceFolders[0] === '' ? ' · current' : ''}</option>{folders.map((folder) => <option key={folder.id} value={folder.id}>{folder.name}{sourceFolders.length === 1 && sourceFolders[0] === folder.id ? ' · current' : ''}</option>)}</select></label></> : null}{localError && <p className="error-banner" role="alert">{localError}</p>}<div className="files-dialog-actions"><button type="button" className="files-secondary" disabled={saving} onClick={() => setModal(null)}>Cancel</button><button className="primary-button" type="submit" disabled={saving || (modal.kind === 'move' && (moveUnchanged || destination === '__choose__')) || ((modal.kind === 'create' || modal.kind === 'rename') && !name.trim())}>{saving ? 'Saving…' : modal.kind === 'create' ? 'Create folder' : modal.kind === 'rename' ? 'Save changes' : modal.kind === 'delete' ? 'Remove folder' : moveUnchanged ? 'Already in this folder' : 'Move papers'}</button></div></form></dialog>}
  </main>;
}

'use client';

import { useEffect, useRef, useState } from 'react';
import AppSelect from './app-select';
import type { Folder, LibraryPaper } from './library-dashboard';

export type DiscoveredPaper = {
  title: string; authors: string[]; year: string; abstract: string; source: string;
  source_url: string; pdf_urls: string[]; arxiv_id: string | null; doi: string | null;
  existing_document_id: string | null;
};

async function payload<T>(response: Response): Promise<T> {
  const body = await response.json();
  if (!response.ok) throw new Error(typeof body.detail === 'string' ? body.detail : 'The request failed. Please try again.');
  return body as T;
}

export default function PaperDiscovery({ query, folders, papers, initialFolderId, onImported, onOpen }: {
  query: string; folders: Folder[]; papers: LibraryPaper[]; initialFolderId: string | null;
  onImported: (paper: LibraryPaper) => void; onOpen: (paper: LibraryPaper) => void;
}) {
  const [results, setResults] = useState<DiscoveredPaper[]>([]);
  const [warnings, setWarnings] = useState<string[]>([]);
  const [searched, setSearched] = useState<string | null>(null);
  const [searching, setSearching] = useState(false);
  const [review, setReview] = useState<DiscoveredPaper | null>(null);
  const [pdfUrl, setPdfUrl] = useState('');
  const [chosenFolder, setChosenFolder] = useState<string | null>(null);
  const folderId = chosenFolder ?? initialFolderId ?? '';
  const [resolving, setResolving] = useState(false);
  const [importing, setImporting] = useState(false);
  const [ready, setReady] = useState<LibraryPaper | null>(null);
  const [error, setError] = useState('');
  const request = useRef<AbortController | null>(null);
  const mounted = useRef(true);

  useEffect(() => { mounted.current = true; return () => { mounted.current = false; request.current?.abort(); }; }, []);

  async function search() {
    if (!query.trim() || importing) return;
    request.current?.abort();
    const controller = new AbortController(); request.current = controller;
    setSearching(true); setError(''); setReview(null); setReady(null); setWarnings([]);
    try {
      const data = await payload<{ results: DiscoveredPaper[]; warnings: string[] }>(await fetch(`/api/paper-discovery/search?q=${encodeURIComponent(query.trim())}`, { signal: controller.signal }));
      if (controller.signal.aborted) return;
      setResults(data.results); setWarnings(data.warnings); setSearched(query.trim());
    } catch (reason) {
      if (!controller.signal.aborted) setError(reason instanceof Error ? reason.message : 'Search failed. Try again.');
    } finally { if (!controller.signal.aborted) setSearching(false); }
  }

  async function reviewPaper(paper: DiscoveredPaper) {
    request.current?.abort();
    const controller = new AbortController(); request.current = controller;
    setReview(paper); setPdfUrl(paper.pdf_urls[0] ?? ''); setReady(null); setError(''); setResolving(false);
    if (paper.existing_document_id) return;
    setResolving(true);
    try {
      const resolved = await payload<DiscoveredPaper>(await fetch('/api/paper-discovery/resolve', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(paper), signal: controller.signal }));
      if (controller.signal.aborted) return;
      setReview(resolved); setPdfUrl(resolved.pdf_urls[0] ?? '');
      setResults((current) => current.map((item) => item.source_url === paper.source_url ? resolved : item));
    } catch (reason) {
      if (!controller.signal.aborted) setError(reason instanceof Error ? reason.message : 'Could not check the source. You can still try an available PDF link.');
    } finally { if (!controller.signal.aborted) setResolving(false); }
  }

  async function add() {
    if (!review || !pdfUrl || importing) return;
    setImporting(true); setError('');
    try {
      const paper = await payload<LibraryPaper>(await fetch('/api/paper-discovery/import', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ paper: review, pdf_url: pdfUrl, folder_id: folderId || null }) }));
      onImported(paper);
      if (!mounted.current) return;
      setReady(paper);
      setResults((current) => current.map((item) => item.source_url === review.source_url ? { ...item, existing_document_id: paper.id } : item));
    } catch (reason) {
      if (mounted.current) setError(reason instanceof Error ? reason.message : 'Could not import this PDF.');
    } finally { if (mounted.current) setImporting(false); }
  }

  const saved = ready ?? papers.find((paper) => paper.id === review?.existing_document_id);
  return <div className="paper-discovery">
    <form className="discovery-search" onSubmit={(event) => { event.preventDefault(); void search(); }}>
      <div><strong>Find your next paper</strong><p>Search arXiv and the web by title, keywords, DOI, or arXiv link. Queries are sent online only when you search.</p></div>
      <button className="primary-button" type="submit" disabled={!query.trim() || searching || importing}>{searching ? 'Searching…' : 'Search online'}</button>
    </form>
    {error && <p className="error-banner" role="alert">{error}</p>}
    {warnings.length > 0 && <div className="discovery-warnings" role="status">{warnings.map((warning) => <p key={warning}>{warning}</p>)}</div>}
    <div className="discovery-body" aria-busy={searching || resolving || importing}>
      {searching ? <div className="spotlight-empty" role="status"><strong>Searching for papers…</strong><span>Checking arXiv and web sources.</span></div> : review ? <section className="discovery-review" aria-label="Review paper">
        <button type="button" className="discovery-back" disabled={importing} onClick={() => { request.current?.abort(); setReview(null); setError(''); setResolving(false); }}>← Back to results</button>
        <div className="discovery-source">{review.source}{review.year && ` · ${review.year}`}{review.arxiv_id && ` · ${review.arxiv_id}`}</div>
        <h2>{review.title}</h2>
        {review.authors.length > 0 && <p className="discovery-authors">{review.authors.join(', ')}</p>}
        {review.doi && <p className="discovery-authors">DOI: {review.doi}</p>}
        {review.abstract && <p className="discovery-abstract">{review.abstract}</p>}
        <div className="discovery-links"><a href={review.source_url} target="_blank" rel="noopener noreferrer" referrerPolicy="no-referrer">View source ↗</a>{pdfUrl && <a href={pdfUrl} target="_blank" rel="noopener noreferrer" referrerPolicy="no-referrer">Preview PDF ↗</a>}</div>
        <div className="discovery-import">
          {saved ? <div className="discovery-ready" role="status"><strong>{ready ? 'Ready to read' : 'Already in your library'}</strong><p>{ready ? 'Your local PDF is ready. Existing copies keep their notes, chats, and folder.' : 'Open the saved copy with its notes and chats.'}</p><button className="primary-button" type="button" onClick={() => onOpen(saved)}>Open in reader →</button></div> : resolving ? <p role="status">Looking for a PDF link on the source page…</p> : review.pdf_urls.length > 0 ? <>
            <label className="discovery-label" htmlFor="discovery-pdf">PDF source</label>
            {review.pdf_urls.length > 1 ? <select id="discovery-pdf" value={pdfUrl} disabled={importing} onChange={(event) => setPdfUrl(event.target.value)}>{review.pdf_urls.map((url) => <option value={url} key={url}>{url}</option>)}</select> : <p className="discovery-url">{pdfUrl}</p>}
            <label className="discovery-label">Add to folder<AppSelect disabled={importing} label="Destination folder" value={folderId} onChange={setChosenFolder} options={[{ value: '', label: 'Unfiled' }, ...folders.map((folder) => ({ value: folder.id, label: folder.name }))]} /></label>
            <button className="primary-button" type="button" disabled={importing || !pdfUrl} onClick={() => void add()}>{importing ? 'Downloading & processing…' : 'Download & add to library'}</button>
            <p className="discovery-footnote" role="status">{importing ? 'Keep this window open while Adam saves the PDF and extracts its text.' : 'Review the PDF before adding it. The file will be stored locally.'}</p>
          </> : <><strong>No downloadable PDF found</strong><p>Open the source to check for an accessible copy. You can download it yourself and use Add PDF in the library.</p><button className="files-secondary" type="button" onClick={() => void reviewPaper(review)}>Check for PDF again</button></>}
        </div>
      </section> : searched !== null ? <>
        <div className="discovery-result-heading">{results.length} {results.length === 1 ? 'result' : 'results'} for “{searched}”</div>
        {results.map((paper) => <button type="button" className="discovery-result" key={paper.source_url} onClick={() => void reviewPaper(paper)}><span className="discovery-source">{paper.source}{paper.year && ` · ${paper.year}`}{paper.arxiv_id && ` · ${paper.arxiv_id}`}{paper.existing_document_id ? ' · In library' : paper.pdf_urls.length ? ' · PDF link available' : ' · Check source for PDF'}</span><strong>{paper.title}</strong>{paper.authors.length > 0 && <span className="discovery-authors">{paper.authors.slice(0, 4).join(', ')}{paper.authors.length > 4 ? ' et al.' : ''}</span>}{paper.abstract && <span className="discovery-snippet">{paper.abstract}</span>}<span className="discovery-review-action">Review paper →</span></button>)}
        {results.length === 0 && <div className="spotlight-empty"><strong>No papers found</strong><span>Try a shorter title, author name, or arXiv identifier.{warnings.length > 0 ? ' Some search sources are currently unavailable.' : ''}</span></div>}
      </> : <div className="spotlight-empty"><strong>A paper title is a good place to start</strong><span>Try “Attention Is All You Need” or paste an arXiv link.<br />You choose what to download after reviewing the results.</span></div>}
    </div>
  </div>;
}

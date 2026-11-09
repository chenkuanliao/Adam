import { test } from 'node:test';
import assert from 'node:assert/strict';
import React, { act, useState } from 'react';
import { createRoot } from 'react-dom/client';
import { JSDOM } from 'jsdom';
import LibraryDashboard, { type Folder, type LibraryPaper } from '../app/library-dashboard';

test('browse nested archived folders, restore a paper to Unfiled, and restore the remaining folder', async () => {
  const dom = new JSDOM('<!doctype html><div id="root"></div>', { url: 'http://localhost' });
  for (const key of ['window', 'document', 'Node', 'HTMLElement', 'navigator']) {
    Object.defineProperty(globalThis, key, { value: dom.window[key as keyof Window], configurable: true });
  }
  Object.assign(globalThis, { React, IS_REACT_ACT_ENVIRONMENT: true });
  const folders: Folder[] = [
    { id: 'parent', name: 'Archived project', parent_id: null, color: '#8c9d65', icon: 'folder', created_at: '2026-10-01', archived_at: '2026-10-25' },
    { id: 'child', name: 'Nested folder', parent_id: 'parent', color: '#8c9d65', icon: 'folder', created_at: '2026-10-01', archived_at: null },
  ];
  const papers: LibraryPaper[] = ['Take out', 'Keep inside'].map((name, i) => ({
    id: `paper-${i}`, original_name: `${name}.pdf`, folder_id: 'child', byte_size: 1024, page_count: 1,
    status: 'ready', created_at: '2026-10-01', updated_at: '2026-10-01', archived_at: null,
  }));
  const calls: { path: string; body: unknown }[] = [];
  const originalFetch = globalThis.fetch;
  globalThis.fetch = async (path, options) => {
    const body = options?.body ? JSON.parse(String(options.body)) : null;
    calls.push({ path: String(path), body });
    if (String(path) === '/api/document-archive') {
      return Response.json(body.document_ids.map((id: string) => ({ ...papers.find(p => p.id === id), folder_id: null, archived_at: null })));
    }
    if (String(path) === '/api/folders/parent') {
      folders[0] = { ...folders[0], archived_at: null };
      return Response.json(folders[0]);
    }
    assert.equal(String(path), '/api/folders');
    return Response.json(folders);
  };
  function Harness() {
    const [items, setItems] = useState(papers);
    return <LibraryDashboard papers={items} loading={false} uploading={false} error="" brand={null} settings={null}
      preview={() => null} onOpen={() => {}} onUpload={async () => {}} onDelete={async () => {}}
      onUpdate={updated => setItems(current => current.map(p => updated.find(item => item.id === p.id) ?? p))}
      onSearch={() => {}} onFindPapers={() => {}} />;
  }
  let root = createRoot(document.getElementById('root')!);
  const click = async (selector: string) => {
    const button = document.querySelector<HTMLButtonElement>(selector);
    assert.ok(button, `Missing ${selector}`);
    assert.equal(button.disabled, false);
    await act(async () => { button.click(); });
  };
  try {
    await act(async () => { root.render(<Harness />); });
    assert.equal(document.querySelector('[aria-label="Open Take out.pdf"]'), null);
    await click('nav button:nth-child(3)');
    await click('[aria-label="Browse archived folder Archived project"]');
    await click('[aria-label="Browse archived folder Nested folder"]');
    assert.ok(document.querySelector('[aria-label="Open Take out.pdf"]'));
    assert.equal(document.querySelector('[aria-label="Open Take out.pdf"]')?.getAttribute('draggable'), 'false');
    assert.equal(document.querySelector('[aria-label="Restore Take out.pdf"]')?.getAttribute('data-tooltip'), 'Restore to Unfiled');
    assert.equal(document.querySelector('[aria-label="Archive folder"]'), null);
    assert.equal(document.querySelector('[aria-label="Move Take out.pdf"]'), null);

    // The saved archive folder remains browsable after reload.
    await act(async () => { root.unmount(); });
    root = createRoot(document.getElementById('root')!);
    await act(async () => { root.render(<Harness />); });
    assert.equal(document.querySelector('h1')?.textContent, 'Nested folder');
    await click('[aria-label="Restore Take out.pdf"]');
    assert.deepEqual(calls.find(c => c.path === '/api/document-archive')?.body, { document_ids: ['paper-0'], archived: false });
    assert.equal(document.querySelector('[aria-label="Open Take out.pdf"]'), null);
    assert.ok(document.querySelector('[aria-label="Open Keep inside.pdf"]'));
    assert.ok(document.querySelector('[role="status"]')?.textContent?.includes('Unfiled'));
    await click('nav button:nth-child(2)');
    assert.ok(document.querySelector('[aria-label="Open Take out.pdf"]'));
    assert.equal(document.querySelector('[aria-label="Open Keep inside.pdf"]'), null);
    await click('nav button:nth-child(3)');
    await click('[aria-label="Restore Archived project"]');
    await click('nav button:first-child');
    assert.ok(document.querySelector('[aria-label="Open Keep inside.pdf"]'));
    assert.ok(document.querySelector('[aria-label="Open Take out.pdf"]'));
  } finally {
    await act(async () => { root.unmount(); });
    globalThis.fetch = originalFetch;
    dom.window.close();
  }
});

'use client';

import { useCallback, useEffect, useRef, useState } from 'react';
import { EditorContent, useEditor } from '@tiptap/react';
import StarterKit from '@tiptap/starter-kit';
import TaskList from '@tiptap/extension-task-list';
import TaskItem from '@tiptap/extension-task-item';
import { requestAppDialog } from './app-dialog';
import { SourceQuote, insertSourceQuote, migrateNoteHtml } from './source-quote';

type Quote = { id: string; text: string; page: number | null };
type PaperNote = { content_html: string; revision: number; detail?: string };

export default function PaperNoteEditor(props: { documentId: string; paperName: string; pendingQuote: Quote | null; onQuoteConsumed: () => void }) {
  // A new document gets its own editor, load request, and save queue.
  return <DocumentNote key={props.documentId} {...props} />;
}

function DocumentNote({ documentId, paperName, pendingQuote, onQuoteConsumed }: { documentId: string; paperName: string; pendingQuote: Quote | null; onQuoteConsumed: () => void }) {
  const [status, setStatus] = useState<'loading' | 'saved' | 'saving' | 'error'>('loading');
  const [errorMessage, setErrorMessage] = useState('');
  const [empty, setEmpty] = useState(true);
  const revision = useRef(0);
  const loaded = useRef(false);
  const dirty = useRef(false);
  const generation = useRef(0);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const queue = useRef(Promise.resolve());
  const insertedQuote = useRef<string | null>(null);
  const saveRef = useRef<() => void>(() => {});
  const editor = useEditor({
    immediatelyRender: false,
    extensions: [StarterKit.configure({ heading: { levels: [1, 2, 3, 4, 5] }, link: { openOnClick: false } }), TaskList, TaskItem.configure({ nested: true }), SourceQuote],
    editable: false,
    editorProps: { attributes: { class: 'paper-note-editor', role: 'textbox', 'aria-multiline': 'true', 'aria-label': 'Paper note' } },
    onUpdate: ({ editor }) => {
      if (!loaded.current) return;
      dirty.current = true; generation.current += 1;
      setEmpty(editor.isEmpty); setStatus('saving');
      if (timer.current) clearTimeout(timer.current);
      timer.current = setTimeout(() => saveRef.current(), 650);
    },
    onBlur: () => saveRef.current(),
  });

  const save = useCallback(() => {
    if (!editor || !loaded.current || !dirty.current) return;
    if (timer.current) clearTimeout(timer.current);
    const content_html = editor.getHTML();
    const plain_text = editor.getText();
    const version = generation.current;
    dirty.current = false;
    setStatus('saving'); setErrorMessage('');
    queue.current = queue.current.then(async () => {
      const body = JSON.stringify({ content_html, plain_text, revision: revision.current });
      // Browsers cap keepalive request bodies at 64 KiB. Larger notes still
      // save normally, while small pending saves can finish during navigation.
      const response = await fetch(`/api/documents/${documentId}/paper-note`, { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body, keepalive: new Blob([body]).size < 60_000 });
      const payload = await response.json() as PaperNote;
      if (!response.ok) throw new Error(payload.detail ?? 'Could not save this note.');
      revision.current = payload.revision;
      if (version === generation.current) setStatus('saved');
    }).catch((reason) => {
      dirty.current = true;
      setErrorMessage(reason instanceof Error ? reason.message : 'Could not save this note.'); setStatus('error');
    });
  }, [documentId, editor]);
  useEffect(() => { saveRef.current = save; }, [save]);

  useEffect(() => {
    if (!editor) return;
    let cancelled = false;
    void fetch(`/api/documents/${documentId}/paper-note`).then(async (response) => {
      const payload = await response.json() as PaperNote;
      if (!response.ok) throw new Error(payload.detail ?? 'Could not load this note.');
      if (cancelled) return;
      revision.current = payload.revision;
      editor.commands.setContent(migrateNoteHtml(payload.content_html), { emitUpdate: false });
      editor.setEditable(true);
      loaded.current = true;
      setEmpty(editor.isEmpty); setStatus('saved');
    }).catch((reason) => { if (!cancelled) { setErrorMessage(reason instanceof Error ? reason.message : 'Could not load this note.'); setStatus('error'); } });
    return () => { cancelled = true; saveRef.current(); if (timer.current) clearTimeout(timer.current); };
  }, [documentId, editor]);

  useEffect(() => {
    if (!editor || !loaded.current || !pendingQuote || insertedQuote.current === pendingQuote.id) return;
    insertedQuote.current = pendingQuote.id;
    insertSourceQuote(editor, pendingQuote);
    onQuoteConsumed();
  }, [editor, pendingQuote, status, onQuoteConsumed]);

  async function addLink() {
    if (!editor) return;
    const selection = editor.state.selection;
    const href = await requestAppDialog({ title: 'Add a link', input: true, confirm: 'Insert link' });
    if (href && /^https?:\/\//i.test(href)) editor.chain().focus().setTextSelection({ from: selection.from, to: selection.to }).setLink({ href }).run();
  }

  return <section className="paper-note-mode">
    <header className="paper-note-header"><div><span>Paper note</span><h2>{paperName}</h2></div><small className={`paper-note-status ${status}`} role="status">{status === 'loading' ? 'Loading…' : status === 'saving' ? 'Saving…' : status === 'error' ? 'Not saved' : 'Saved'}</small></header>
    <div className="paper-note-toolbar" role="toolbar" aria-label="Note formatting" onMouseDown={(event) => event.preventDefault()}>
      <button type="button" title="Heading 1" onClick={() => editor?.chain().focus().toggleHeading({ level: 1 }).run()}>H1</button><button type="button" title="Heading 2" onClick={() => editor?.chain().focus().toggleHeading({ level: 2 }).run()}>H2</button><button type="button" title="Paragraph" onClick={() => editor?.chain().focus().setParagraph().run()}>¶</button><i />
      <button type="button" title="Bold" onClick={() => editor?.chain().focus().toggleBold().run()}><strong>B</strong></button><button type="button" title="Italic" onClick={() => editor?.chain().focus().toggleItalic().run()}><em>I</em></button><button type="button" title="Link" onClick={addLink}>↗</button><i />
      <button type="button" title="Bulleted list" onClick={() => editor?.chain().focus().toggleBulletList().run()}>• List</button><button type="button" title="Numbered list" onClick={() => editor?.chain().focus().toggleOrderedList().run()}>1. List</button><button type="button" title="Checklist" onClick={() => editor?.chain().focus().toggleTaskList().run()}>☐</button><button type="button" title="Quote" onClick={() => editor?.chain().focus().toggleBlockquote().run()}>❝</button>
      <span /><button type="button" title="Undo" aria-label="Undo" onClick={() => editor?.chain().focus().undo().run()}>↶</button><button type="button" title="Redo" aria-label="Redo" onClick={() => editor?.chain().focus().redo().run()}>↷</button>
    </div>
    <div className="paper-note-canvas"><EditorContent editor={editor} className="paper-note-content" /><div className={`paper-note-placeholder${empty ? ' visible' : ''}`} aria-hidden="true"><strong>Start writing about this paper…</strong><span>Use # for headings, - for bullets, 1. for numbers, or - [ ] for tasks. Click here to place your cursor, then select PDF text and press Q.</span></div></div>
    {errorMessage && <button type="button" className="paper-note-error" onClick={save}>{errorMessage} Click to retry.</button>}
  </section>;
}

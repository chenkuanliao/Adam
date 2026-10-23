import { Node, type Editor } from '@tiptap/core';
import { Plugin } from '@tiptap/pm/state';

export type QuoteSource = {
  text: string;
  page: number | null;
  documentId?: string | null;
  rects?: { left: number; top: number; width: number; height: number }[];
};

function sourceRects(value: string | null): QuoteSource['rects'] {
  try {
    const rects: unknown = JSON.parse(value ?? '[]');
    if (!Array.isArray(rects)) return [];
    return rects.filter((rect) => rect && ['left', 'top', 'width', 'height'].every((key) => typeof rect[key] === 'number' && Number.isFinite(rect[key]) && rect[key] >= 0 && rect[key] <= 1) && rect.width > 0 && rect.height > 0);
  } catch { return []; }
}

function citation(page: number | null) {
  return page ? `Quoted from paper · Page ${page}` : 'Quoted from paper';
}

// Source text is stored as attributes on a leaf node, never as editable content.
// Legacy quotes are recognized by their citation when loading older notes.
export const SourceQuote = Node.create<{ onShowInPdf: ((quote: QuoteSource) => void) | null }>({
  addOptions() { return { onShowInPdf: null }; },
  name: 'sourceQuote',
  group: 'block',
  atom: true,
  draggable: true,
  selectable: true,
  addAttributes() {
    return { text: { default: '', rendered: false }, page: { default: null, rendered: false }, documentId: { default: null, rendered: false }, rects: { default: [], rendered: false } };
  },
  parseHTML() {
    return [{
      tag: 'blockquote', priority: 100,
      getAttrs: (element) => {
        if (!element.querySelector('cite') && !element.hasAttribute('data-source-quote')) return false;
        const page = /Page (\d+)/.exec(element.querySelector('cite')?.textContent ?? '');
        return { text: element.querySelector('p')?.textContent ?? '', page: page ? Number(page[1]) : null, documentId: element.getAttribute('data-source-document'), rects: sourceRects(element.getAttribute('data-source-rects')) };
      },
    }];
  },
  renderHTML({ node }) {
    return ['blockquote', { 'data-source-quote': '', 'data-source-document': node.attrs.documentId, 'data-source-rects': JSON.stringify(node.attrs.rects), class: 'paper-source-quote', contenteditable: 'false' },
      ['p', {}, node.attrs.text], ['cite', {}, citation(node.attrs.page)]];
  },
  renderText({ node }) { return `${node.attrs.text}\n${citation(node.attrs.page)}`; },
  addNodeView() {
    const onShowInPdf = this.options.onShowInPdf;
    return ({ node, editor, getPos }) => {
      const dom = document.createElement('blockquote');
      dom.className = 'paper-source-quote';
      dom.setAttribute('data-source-quote', '');
      dom.contentEditable = 'false';
      const tools = document.createElement('div');
      tools.className = 'source-quote-tools';
      const handle = document.createElement('span');
      handle.className = 'source-quote-handle';
      handle.textContent = '⠿';
      handle.title = 'Drag to move quote';
      handle.setAttribute('aria-label', 'Drag to move quote');
      tools.append(handle);
      function button(label: string, text: string, action: () => void) {
        const button = document.createElement('button');
        button.type = 'button'; button.title = label; button.setAttribute('aria-label', label); button.textContent = text;
        button.addEventListener('mousedown', (event) => event.preventDefault());
        button.addEventListener('click', action);
        tools.append(button);
      }
      function insertLine(after: boolean) {
        const pos = getPos();
        if (pos === undefined) return;
        const at = pos + (after ? node.nodeSize : 0);
        editor.chain().focus().insertContentAt(at, { type: 'paragraph' }).setTextSelection(at + 1).run();
      }
      function move(direction: -1 | 1) {
        const pos = getPos();
        if (pos === undefined) return;
        const $pos = editor.state.doc.resolve(pos);
        const adjacent = direction === -1 ? $pos.nodeBefore : editor.state.doc.resolve(pos + node.nodeSize).nodeAfter;
        if (!adjacent) return;
        const to = direction === -1 ? pos - adjacent.nodeSize : pos + adjacent.nodeSize;
        editor.chain().focus().command(({ tr }) => {
          tr.delete(pos, pos + node.nodeSize).insert(to, node);
          return true;
        }).setNodeSelection(to).run();
      }
      if (onShowInPdf && node.attrs.page) button('Show quote in PDF', '↗ Show in PDF', () => onShowInPdf(node.attrs as QuoteSource));
      button('Move quote up', '↑', () => move(-1));
      button('Move quote down', '↓', () => move(1));
      button('Write above quote', '+ Above', () => insertLine(false));
      button('Write below quote', '+ Below', () => insertLine(true));
      const content = document.createElement('p'); content.textContent = node.attrs.text;
      const cite = document.createElement('cite'); cite.textContent = citation(node.attrs.page);
      dom.append(tools, content, cite);
      return { dom, stopEvent: (event) => (event.target as HTMLElement).closest('button') !== null };
    };
  },
  addProseMirrorPlugins() {
    return [new Plugin({
      // Keep a real editable paragraph at both document edges. Gap cursors and
      // Enter also work between blocks; these paragraphs make empty notes obvious.
      appendTransaction: (_transactions, _oldState, state) => {
        const tr = state.tr;
        if (tr.doc.firstChild?.type.name === 'sourceQuote') tr.insert(0, state.schema.nodes.paragraph.create());
        if (tr.doc.lastChild?.type.name === 'sourceQuote') tr.insert(tr.doc.content.size, state.schema.nodes.paragraph.create());
        return tr.docChanged ? tr : null;
      },
    })];
  },
});

export function insertSourceQuote(editor: Editor, quote: QuoteSource) {
  // Editor state retains its selection while focus moves to the PDF. Collapse a
  // selected note range so quoting cannot accidentally erase the user's writing.
  const { from } = editor.state.selection;
  return editor.chain().focus().setTextSelection(from).insertContent({ type: 'sourceQuote', attrs: quote }).run();
}

export function migrateNoteHtml(html: string) {
  const doc = new DOMParser().parseFromString(html, 'text/html');
  // Earlier checklists used ordinary UL/LI/INPUT HTML.
  for (const list of doc.querySelectorAll('ul')) {
    if (!list.querySelector(':scope > li > input[type="checkbox"]')) continue;
    list.setAttribute('data-type', 'taskList');
    for (const item of list.querySelectorAll(':scope > li')) {
      const checkbox = item.querySelector(':scope > input[type="checkbox"]');
      if (!checkbox) continue;
      item.setAttribute('data-type', 'taskItem');
      item.setAttribute('data-checked', String(checkbox.hasAttribute('checked')));
      checkbox.remove();
    }
  }
  return doc.body.innerHTML;
}

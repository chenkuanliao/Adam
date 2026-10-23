import { test } from 'node:test';
import assert from 'node:assert/strict';
import { JSDOM } from 'jsdom';
import { Editor, type JSONContent } from '@tiptap/core';
import StarterKit from '@tiptap/starter-kit';
import TaskList from '@tiptap/extension-task-list';
import TaskItem from '@tiptap/extension-task-item';
import { SourceQuote, insertSourceQuote, migrateNoteHtml } from '../app/source-quote';

const dom = new JSDOM('<!doctype html><html><body></body></html>', { url: 'http://localhost' });
for (const key of ['window', 'document', 'DOMParser', 'Node', 'HTMLElement', 'Element', 'MutationObserver', 'getComputedStyle', 'navigator']) {
  Object.defineProperty(globalThis, key, { value: dom.window[key as keyof Window], configurable: true });
}
globalThis.requestAnimationFrame = () => 0;
globalThis.cancelAnimationFrame = () => {};
function make(content = '<p></p>') {
  return new Editor({ extensions: [StarterKit, TaskList, TaskItem, SourceQuote], content: migrateNoteHtml(content) });
}
function json(editor: Editor): JSONContent { return editor.getJSON(); }
const quote = { text: 'Evidence <script>literal</script> & intact.', page: 3 };

test('quote inserts at retained cursor and splits paragraph without losing text', () => {
  const editor = make('<p>Before after</p><p>Last paragraph</p>');
  editor.commands.setTextSelection(8); editor.commands.blur(); insertSourceQuote(editor, quote);
  const content = json(editor).content!;
  assert.deepEqual(content.map(n => n.type), ['paragraph', 'sourceQuote', 'paragraph', 'paragraph']);
  assert.equal(content[0].content![0].text, 'Before ');
  assert.equal(content[2].content![0].text, 'after');
  assert.equal(content[3].content![0].text, 'Last paragraph');
  assert.equal(content[1].attrs!.text, quote.text);
  assert.equal(content[1].content, undefined);
  assert.equal(editor.schema.nodes.sourceQuote.spec.atom, true);
  assert.equal(editor.view.dom.querySelector('blockquote')!.contentEditable, 'false');
  editor.destroy();
});

test('empty note has editable space above and below, including after reload', () => {
  const editor = make(); insertSourceQuote(editor, quote);
  assert.deepEqual(json(editor).content!.map(n => n.type), ['paragraph', 'sourceQuote', 'paragraph']);
  editor.commands.setTextSelection(1); editor.commands.insertContent('Above');
  editor.commands.setTextSelection(editor.state.doc.content.size - 1); editor.commands.insertContent('Below');
  const saved = editor.getHTML(); const restored = make(saved);
  assert.equal(json(restored).content![0].content![0].text, 'Above');
  assert.equal(json(restored).content![2].content![0].text, 'Below');
  assert.equal(json(restored).content![1].attrs!.text, quote.text);
  assert.ok(!saved.includes('<script>'));
  assert.ok(restored.getText().includes(quote.text));
  editor.destroy(); restored.destroy();
});

test('legacy quote and checklist migrate, ordinary blockquotes remain editable', () => {
  const editor = make('<h2>Finding</h2><blockquote><p>Old excerpt</p><cite>Quoted from paper · Page 12</cite></blockquote><blockquote><p>My own quote</p></blockquote><ul><li><input type="checkbox" checked>Finished</li></ul>');
  const nodes = json(editor).content!;
  assert.equal(nodes[0].type, 'heading'); assert.equal(nodes[1].type, 'sourceQuote');
  assert.deepEqual({ ...nodes[1].attrs }, { text: 'Old excerpt', page: 12 });
  assert.equal(nodes[2].type, 'blockquote'); assert.equal(nodes[3].type, 'taskList');
  assert.equal(nodes[3].content![0].attrs!.checked, true); editor.destroy();
});

test('insertion can be undone and redone as one edit', () => {
  const editor = make('<p>Keep me</p>'); editor.commands.setTextSelection(5); insertSourceQuote(editor, quote);
  editor.commands.undo(); assert.equal(editor.getHTML(), '<p>Keep me</p>');
  editor.commands.redo(); assert.equal(json(editor).content!.filter(n => n.type === 'sourceQuote').length, 1); editor.destroy();
});

test('HTML clipboard round trip preserves objects and surrounding writing', () => {
  const editor = make('<p>Intro</p>'); insertSourceQuote(editor, quote);
  const { dom: clipboard } = editor.view.serializeForClipboard(editor.state.doc.slice(0, editor.state.doc.content.size));
  const pasted = make(clipboard.innerHTML);
  assert.deepEqual(json(pasted), json(editor));
  assert.ok(!clipboard.innerHTML.includes('source-quote-tools')); editor.destroy(); pasted.destroy();
});

test('move and write-above/below controls work', () => {
  const editor = make('<p>First</p><blockquote><p>Excerpt</p><cite>Quoted from paper</cite></blockquote><p>Last</p>');
  const click = (label: string) => (editor.view.dom.querySelector(`[aria-label="${label}"]`) as HTMLButtonElement).click();
  click('Move quote down');
  assert.deepEqual(json(editor).content!.map(n => n.type), ['paragraph', 'paragraph', 'sourceQuote', 'paragraph']);
  click('Write above quote'); editor.commands.insertContent('Inserted above');
  click('Write below quote'); editor.commands.insertContent('Inserted below');
  assert.ok(editor.getHTML().includes('<p>Inserted above</p>'));
  assert.ok(editor.getHTML().includes('<p>Inserted below</p>')); editor.destroy();
});

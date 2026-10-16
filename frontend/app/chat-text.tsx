'use client';

import { memo, useLayoutEffect, useMemo, useSyncExternalStore, type InputHTMLAttributes, type Ref, type TextareaHTMLAttributes } from 'react';
import ReactMarkdown, { type Components } from 'react-markdown';
import remarkGfm from 'remark-gfm';
import remarkMath from 'remark-math';
import rehypeKatex from 'rehype-katex';
import type { TextStore } from './stream-text';

type CitationSources = { sources: Array<{ url: string }> } | null | undefined;

// Module-level so ReactMarkdown receives identical plugin/component props on every render.
const REMARK_PLUGINS = [remarkGfm, remarkMath];
const REHYPE_PLUGINS = [rehypeKatex];
// eslint-disable-next-line @typescript-eslint/no-unused-vars -- keep react-markdown's AST `node` off the DOM
const MARKDOWN_COMPONENTS: Components = { a: ({ children: linkText, node, ...props }) => <a {...props} target="_blank" rel="noreferrer">{linkText}</a> };

export function useTextStore(store: TextStore) {
  return useSyncExternalStore(store.subscribe, store.get, store.get);
}

// Re-renders the caller only when the text toggles between empty and non-empty.
export function useHasText(store: TextStore, trimmed = false) {
  const hasText = () => (trimmed ? store.get().trim() : store.get()) !== '';
  return useSyncExternalStore(store.subscribe, hasText, hasText);
}

export function normalizeMathDelimiters(markdown: string): string {
  const lines = markdown.split('\n');
  let fence: { marker: string; length: number } | null = null;
  let prose = '';
  let result = '';
  const flushProse = () => {
    result += prose.replace(/(`+)[\s\S]*?\1|\\\[([\s\S]*?)\\\]|\\\(([^\n]*?)\\\)/g, (match, _ticks: string, display: string | undefined, inline: string | undefined) => {
      if (display !== undefined) return `\n$$\n${display.trim()}\n$$\n`;
      if (inline !== undefined) return `$${inline}$`;
      return match;
    });
    prose = '';
  };

  for (const [index, line] of lines.entries()) {
    const suffix = index < lines.length - 1 ? '\n' : '';
    const opening = /^ {0,3}(`{3,}|~{3,})/.exec(line);
    if (fence) {
      result += line + suffix;
      if (new RegExp(`^ {0,3}${fence.marker}{${fence.length},}[ \\t]*$`).test(line)) fence = null;
    } else if (opening) {
      flushProse();
      fence = { marker: opening[1][0], length: opening[1].length };
      result += line + suffix;
    } else {
      prose += line + suffix;
    }
  }
  flushProse();
  return result;
}

export function prepareAnswerMarkdown(answer: string, web: CitationSources): string {
  const linked = web ? answer.replace(/\[W(\d+)\]/g, (marker, number: string) => {
    const url = web.sources[Number(number) - 1]?.url;
    return url ? `[${marker}](<${url.replaceAll('>', '%3E')}>)` : marker;
  }) : answer;
  return normalizeMathDelimiters(linked);
}

// Memoized: saved turns keep the same answer string and web object, so a reader
// re-render never re-parses their Markdown or re-typesets their math.
export const MarkdownAnswer = memo(function MarkdownAnswer({ children, streaming = false, web }: { children: string; streaming?: boolean; web?: CitationSources }) {
  const source = useMemo(() => prepareAnswerMarkdown(children, web), [children, web]);
  const rendered = useMemo(() => <ReactMarkdown remarkPlugins={REMARK_PLUGINS} rehypePlugins={REHYPE_PLUGINS} components={MARKDOWN_COMPONENTS}>{source}</ReactMarkdown>, [source]);
  return <div className="markdown-answer">{rendered}{streaming && <i className="stream-cursor" />}</div>;
});

// Subscribes to the in-flight answer so streamed fragments re-render only this
// card. `onCommit` runs after each committed update (used to follow output).
export function StreamingMarkdown({ store, streaming, web, onCommit }: { store: TextStore; streaming: boolean; web?: CitationSources; onCommit?: () => void }) {
  const text = useTextStore(store);
  useLayoutEffect(() => { onCommit?.(); }, [text, onCommit]);
  return text ? <MarkdownAnswer streaming={streaming} web={web}>{text}</MarkdownAnswer> : <div className="answer-skeleton"><i /><i /><i /></div>;
}

export function DraftTextarea({ store, ...props }: { store: TextStore; ref?: Ref<HTMLTextAreaElement> } & Omit<TextareaHTMLAttributes<HTMLTextAreaElement>, 'value' | 'onChange'>) {
  return <textarea {...props} value={useTextStore(store)} onChange={(event) => store.set(event.target.value)} />;
}

export function DraftInput({ store, ...props }: { store: TextStore; ref?: Ref<HTMLInputElement> } & Omit<InputHTMLAttributes<HTMLInputElement>, 'value' | 'onChange'>) {
  return <input {...props} value={useTextStore(store)} onChange={(event) => store.set(event.target.value)} />;
}

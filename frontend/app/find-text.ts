// Literal, case-insensitive matching. A query can span PDF text fragments.
export function findMatches(text: string, query: string): { start: number; end: number }[] {
  const needle = query.trim();
  if (!needle) return [];
  const escaped = needle.split(/\s+/).map((part) => part.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')).join('\\s+');
  return Array.from(text.matchAll(new RegExp(escaped, 'gi')), (match) => ({ start: match.index, end: match.index + match[0].length }));
}

export function findOffsets(text: string, query: string): number[] {
  return findMatches(text, query).map((match) => match.start);
}

export function textRanges(root: Element, query: string, selector: string): Range[] {
  const ranges: Range[] = [];
  root.querySelectorAll(selector).forEach((block) => {
    const walker = document.createTreeWalker(block, NodeFilter.SHOW_TEXT | NodeFilter.SHOW_ELEMENT);
    const nodes: { node: Text; start: number; end: number }[] = [];
    let text = '';
    while (walker.nextNode()) {
      if (walker.currentNode.nodeType !== Node.TEXT_NODE) {
        if ((walker.currentNode as Element).tagName === 'BR') text += ' ';
        continue;
      }
      const node = walker.currentNode as Text;
      nodes.push({ node, start: text.length, end: text.length + node.length });
      text += node.data;
    }
    for (const { start: offset, end: finish } of findMatches(text, query)) {
      const start = nodes.find((item) => item.end > offset);
      const end = nodes.find((item) => item.end >= finish);
      if (!start || !end) continue;
      const range = document.createRange();
      range.setStart(start.node, offset - start.start);
      range.setEnd(end.node, finish - end.start);
      ranges.push(range);
    }
  });
  return ranges;
}

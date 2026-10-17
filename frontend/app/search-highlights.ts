export type SearchHighlight = { clear(): void; add(range: Range): unknown };
export type HighlightRegistry = { set(name: string, value: SearchHighlight): void; delete(name: string): void };

// Keep two registered objects for the lifetime of a find bar. Clearing their
// ranges explicitly invalidates old paint, including in embedded WebKit views.
// It also releases references to text nodes on pages that leave the DOM.
export function createSearchHighlights(registry: HighlightRegistry, create: () => SearchHighlight, kind: string) {
  const matches = create();
  const active = create();
  const matchName = `find-${kind}`;
  const activeName = `${matchName}-active`;
  registry.set(matchName, matches);
  registry.set(activeName, active);
  function clear() { matches.clear(); active.clear(); }
  return {
    clear,
    replace(ranges: Range[], selected?: Range) {
      clear();
      for (const range of ranges) matches.add(range);
      if (selected) active.add(selected);
    },
    dispose() { clear(); registry.delete(matchName); registry.delete(activeName); },
  };
}

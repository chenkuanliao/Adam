type Rect = { left: number; top: number; width: number; height: number };
type LocatedSelection = { page: number | null; text: string; rects: Rect[] };

/** Page-relative geometry survives reloads and zoom; repeated text is not an identity. */
export function sameHighlightLocation(saved: LocatedSelection, selected: LocatedSelection): boolean {
  if (!saved.page || saved.page !== selected.page || saved.text !== selected.text || !saved.rects.length || saved.rects.length !== selected.rects.length) return false;
  const order = (rects: Rect[]) => [...rects].sort((a, b) => a.top - b.top || a.left - b.left);
  const selectedRects = order(selected.rects);
  return order(saved.rects).every((rect, index) => {
    const other = selectedRects[index];
    if (![rect, other].every((item) => [item.left, item.top, item.width, item.height].every(Number.isFinite) && item.width > 0 && item.height > 0)) return false;
    // Allow subpixel text-layer rounding, but never a neighboring word or line.
    const toleranceX = Math.min(.0008, Math.min(rect.width, other.width) * .1);
    const toleranceY = Math.min(.0008, Math.min(rect.height, other.height) * .1);
    return Math.abs(rect.left - other.left) <= toleranceX
      && Math.abs(rect.left + rect.width - other.left - other.width) <= toleranceX
      && Math.abs(rect.top - other.top) <= toleranceY
      && Math.abs(rect.top + rect.height - other.top - other.height) <= toleranceY;
  });
}

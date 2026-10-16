export type ReadingPosition = { id: string; page: number; offset: number; horizontal: number; zoom: number; fit: boolean; readAt: number };
const KEY = 'adam.readingHistory.v1';
export function readingHistory(): ReadingPosition[] {
  try {
    const records: unknown = JSON.parse(localStorage.getItem(KEY) ?? '[]');
    if (!Array.isArray(records)) return [];
    return records.filter((item): item is ReadingPosition => item && typeof item.id === 'string' && Number.isInteger(item.page) && item.page > 0 && Number.isFinite(item.offset) && item.offset >= 0 && item.offset <= 1 && Number.isFinite(item.horizontal) && item.horizontal >= 0 && item.horizontal <= 1 && Number.isFinite(item.zoom) && item.zoom >= .1 && item.zoom <= 32 && typeof item.fit === 'boolean' && Number.isFinite(item.readAt)).sort((a, b) => b.readAt - a.readAt).slice(0, 30);
  } catch { return []; }
}
export function saveReadingPosition(record: ReadingPosition) {
  try { localStorage.setItem(KEY, JSON.stringify([record, ...readingHistory().filter((item) => item.id !== record.id)].slice(0, 30))); } catch { /* Reading remains usable when storage is disabled or full. */ }
}
export function forgetReadingPosition(id: string) {
  try { localStorage.setItem(KEY, JSON.stringify(readingHistory().filter((item) => item.id !== id))); } catch { /* Optional browser state. */ }
}

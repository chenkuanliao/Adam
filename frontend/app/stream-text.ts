// Text that changes often (drafts, streamed answers) lives outside the reader's
// React state so only the components that display it re-render.
export type TextStore = { get: () => string; set: (value: string) => void; subscribe: (listener: () => void) => () => void };

export function createTextStore(initial = ''): TextStore {
  let value = initial;
  const listeners = new Set<() => void>();
  return {
    get: () => value,
    set: (next) => {
      if (next === value) return;
      value = next;
      for (const listener of [...listeners]) listener();
    },
    subscribe: (listener) => {
      listeners.add(listener);
      return () => { listeners.delete(listener); };
    },
  };
}

export type StreamBatcher = { schedule: () => void; flush: () => void; cancel: () => void };
type Timers = { setTimeout: (callback: () => void, delay: number) => unknown; clearTimeout: (handle: unknown) => void };

export const STREAM_FLUSH_MS = 50;

// Coalesces streamed fragments into at most one UI update per interval.
// `flush` publishes immediately (first fragment, EOF, error); `cancel` drops
// any scheduled publish once the stream is finished or abandoned.
export function createStreamBatcher(publish: () => void, delay = STREAM_FLUSH_MS, timers: Timers = globalThis as unknown as Timers): StreamBatcher {
  let handle: unknown = null;
  let cancelled = false;
  const clear = () => {
    if (handle !== null) timers.clearTimeout(handle);
    handle = null;
  };
  return {
    schedule: () => {
      if (cancelled || handle !== null) return;
      handle = timers.setTimeout(() => { handle = null; if (!cancelled) publish(); }, delay);
    },
    flush: () => {
      clear();
      if (!cancelled) publish();
    },
    cancel: () => {
      clear();
      cancelled = true;
    },
  };
}

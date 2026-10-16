'use client';

import { useEffect, useId, useRef, useState } from 'react';

type Request = { title: string; message?: string; confirm?: string; input?: boolean; destructive?: boolean };
type Pending = Request & { resolve: (result: string | null) => void };
const EVENT = 'adam:request-dialog';
export function requestAppDialog(request: Request): Promise<string | null> {
  return new Promise((resolve) => window.dispatchEvent(new CustomEvent<Pending>(EVENT, { detail: { ...request, resolve } })));
}
export default function AppDialogs() {
  const [pending, setPending] = useState<Pending | null>(null);
  const [value, setValue] = useState('');
  const active = useRef<Pending | null>(null);
  const dialog = useRef<HTMLDialogElement>(null);
  const id = useId();
  function finish(result: string | null) { active.current?.resolve(result); active.current = null; setPending(null); }
  useEffect(() => {
    function receive(event: Event) {
      active.current?.resolve(null);
      const request = (event as CustomEvent<Pending>).detail;
      active.current = request; setPending(request); setValue('');
    }
    window.addEventListener(EVENT, receive);
    return () => { window.removeEventListener(EVENT, receive); active.current?.resolve(null); };
  }, []);
  useEffect(() => { if (pending) dialog.current?.showModal(); }, [pending]);
  if (!pending) return null;
  return <dialog ref={dialog} className="app-dialog" aria-labelledby={id} aria-describedby={pending.message ? `${id}-description` : undefined} onCancel={(event) => { event.preventDefault(); finish(null); }} onClick={(event) => { if (event.target === dialog.current) { const rect = event.currentTarget.getBoundingClientRect(); if (event.clientX < rect.left || event.clientX > rect.right || event.clientY < rect.top || event.clientY > rect.bottom) finish(null); } }}>
    <form onSubmit={(event) => { event.preventDefault(); finish(pending.input ? value.trim() : 'confirmed'); }}>
      <h2 id={id}>{pending.title}</h2>{pending.message && <p id={`${id}-description`}>{pending.message}</p>}
      {pending.input && <label className="field-label">Link URL<input autoFocus aria-label="Link URL" type="url" pattern="https?://.*" placeholder="https://…" value={value} onChange={(event) => setValue(event.target.value)} required /></label>}
      <footer><button type="button" className="secondary-button" autoFocus={!pending.input} onClick={() => finish(null)}>Cancel</button><button className={`primary-button${pending.destructive ? ' destructive-button' : ''}`} type="submit">{pending.confirm ?? 'Confirm'}</button></footer>
    </form>
  </dialog>;
}

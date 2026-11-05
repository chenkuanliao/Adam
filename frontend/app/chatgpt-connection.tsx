'use client';

import { useCallback, useEffect, useRef, useState } from 'react';

export type ChatGPTStatus = {
  signed_in: boolean;
  plan_enabled: boolean;
  email: string | null;
  name: string | null;
  access_expires_at: number | null;
  session_expires_at: number | null;
  last_refreshed_at: number | null;
  registered: boolean;
  usage_paused: boolean;
  usage_message: string | null;
  usage_url: string;
  pending: boolean;
  redirect_uri: string;
  listener_active: boolean;
  listener_error: string | null;
  last_error: string | null;
  revoked?: boolean | null;
};

const POLL_MS = 2000;
const POLL_LIMIT_MS = 10 * 60 * 1000;

function formatDate(seconds: number | null) {
  if (!seconds) return 'unknown';
  return new Date(seconds * 1000).toLocaleString(undefined, { dateStyle: 'medium', timeStyle: 'short' });
}

async function readJson<T>(response: Response, fallback: string): Promise<T> {
  const value = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error((value as { detail?: string }).detail ?? fallback);
  return value as T;
}

/** Sign in with ChatGPT controls. Tokens stay on the server; this only sees status metadata. */
export function ChatGPTConnection({ onChange }: { onChange: (status: ChatGPTStatus) => void }) {
  const [status, setStatus] = useState<ChatGPTStatus | null>(null);
  const [message, setMessage] = useState('');
  const [busy, setBusy] = useState(false);
  const [waiting, setWaiting] = useState(false);
  const [fallbackUrl, setFallbackUrl] = useState('');
  const [callbackUrl, setCallbackUrl] = useState('');
  // The loopback redirect only reaches this server from the machine running Adam.
  // Settings renders only after a client interaction, so reading window here is safe.
  const [remote] = useState(() => typeof window !== 'undefined' && !['localhost', '127.0.0.1', '[::1]'].includes(window.location.hostname));
  const pollRef = useRef<number | null>(null);
  const onChangeRef = useRef(onChange);
  useEffect(() => { onChangeRef.current = onChange; }, [onChange]);

  const apply = useCallback((next: ChatGPTStatus) => { setStatus(next); onChangeRef.current(next); }, []);
  const stopPolling = useCallback(() => { if (pollRef.current !== null) window.clearInterval(pollRef.current); pollRef.current = null; setWaiting(false); }, []);

  useEffect(() => {
    void fetch('/api/chatgpt/status').then((response) => readJson<ChatGPTStatus>(response, 'Could not load ChatGPT status.')).then(setStatus).catch((reason: Error) => setMessage(reason.message));
    return () => { if (pollRef.current !== null) window.clearInterval(pollRef.current); };
  }, []);

  function poll() {
    stopPolling();
    setWaiting(true);
    const started = Date.now();
    pollRef.current = window.setInterval(() => {
      if (Date.now() - started > POLL_LIMIT_MS) { stopPolling(); return; }
      void fetch('/api/chatgpt/status').then((response) => readJson<ChatGPTStatus>(response, '')).then((next) => {
        setStatus(next);
        if (next.signed_in && next.plan_enabled && (next.last_refreshed_at ?? 0) * 1000 >= started - 5000) {
          stopPolling(); apply(next); setFallbackUrl(''); setMessage(`Connected${next.email ? ` as ${next.email}` : ''}`);
        }
      }).catch(() => undefined);
    }, POLL_MS);
  }

  async function signIn() {
    setBusy(true); setMessage('');
    // Open the tab during the click so popup blockers allow it; navigate once the URL is ready.
    const tab = window.open('', '_blank');
    try {
      const value = await readJson<{ authorize_url: string; listener_active: boolean; listener_error: string | null }>(await fetch('/api/chatgpt/sign-in', { method: 'POST' }), 'Could not start ChatGPT sign-in.');
      setFallbackUrl(value.authorize_url);
      if (tab) { tab.opener = null; tab.location.href = value.authorize_url; }
      if (value.listener_error) setMessage(value.listener_error);
      poll();
    } catch (reason) {
      tab?.close();
      setMessage(reason instanceof Error ? reason.message : 'Could not start ChatGPT sign-in.');
    } finally { setBusy(false); }
  }

  async function finishWithUrl() {
    if (!callbackUrl.trim()) return;
    setBusy(true); setMessage('');
    try {
      const next = await readJson<ChatGPTStatus>(await fetch('/api/chatgpt/callback', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ url: callbackUrl.trim() }) }), 'Could not finish sign-in.');
      stopPolling(); apply(next); setCallbackUrl(''); setFallbackUrl('');
      setMessage(`Connected${next.email ? ` as ${next.email}` : ''}`);
    } catch (reason) { setMessage(reason instanceof Error ? reason.message : 'Could not finish sign-in.'); }
    finally { setBusy(false); }
  }

  async function signOut() {
    setBusy(true); setMessage('');
    try {
      const next = await readJson<ChatGPTStatus>(await fetch('/api/chatgpt/sign-out', { method: 'POST' }), 'Could not sign out.');
      stopPolling(); apply(next);
      setMessage(next.revoked === false ? 'Signed out locally. ChatGPT did not confirm revocation; you can disconnect Adam in ChatGPT settings.' : 'Signed out of ChatGPT');
    } catch (reason) { setMessage(reason instanceof Error ? reason.message : 'Could not sign out.'); }
    finally { setBusy(false); }
  }

  async function resume() {
    setBusy(true);
    try { apply(await readJson<ChatGPTStatus>(await fetch('/api/chatgpt/resume', { method: 'POST' }), 'Could not resume.')); setMessage('ChatGPT requests resumed'); }
    catch (reason) { setMessage(reason instanceof Error ? reason.message : 'Could not resume.'); }
    finally { setBusy(false); }
  }

  const connected = Boolean(status?.signed_in && status.plan_enabled);
  return <div className="chatgpt-connection">
    {connected ? <div className="chatgpt-account">
      <div><span>Signed in as</span><strong>{status?.email ?? status?.name ?? 'ChatGPT account'}</strong><small>Access renews automatically · session valid until {formatDate(status?.session_expires_at ?? null)} (extends while Adam runs)</small></div>
      <button type="button" className="secondary-button" disabled={busy} onClick={() => void signOut()}>Sign out</button>
    </div> : <div className="chatgpt-signin">
      <button type="button" className="primary-button" disabled={busy || !status} onClick={() => void signIn()}>{waiting ? 'Waiting for ChatGPT…' : status?.signed_in ? 'Sign in again with ChatGPT' : 'Sign in with ChatGPT'}</button>
      <p>Requires a ChatGPT Plus or Pro plan. Opens ChatGPT in a new tab; approve access, then return here.</p>
      {status?.signed_in && !status.plan_enabled && <p className="title-key-notice">Signed in, but plan usage was not approved. Sign in again and allow Adam to use your plan.</p>}
      {status?.last_error && !status.signed_in && <p className="title-key-notice">{status.last_error}</p>}
      {fallbackUrl && <p className="chatgpt-fallback-link">Tab didn’t open? <a href={fallbackUrl} target="_blank" rel="noreferrer">Open the ChatGPT sign-in page</a>.</p>}
    </div>}
    {status?.usage_paused && <div className="no-key-notice chatgpt-paused" role="alert"><strong>ChatGPT plan limit reached</strong><span>Adam paused ChatGPT requests. Check your usage at <a href={status.usage_url} target="_blank" rel="noreferrer">ChatGPT settings</a>.</span><button type="button" className="secondary-button" disabled={busy} onClick={() => void resume()}>Resume now</button></div>}
    {!connected && <details className="chatgpt-paste" open={remote || waiting || undefined}>
      <summary>Using Adam from another device?</summary>
      <p>ChatGPT redirects to <code>{status?.redirect_uri ?? 'http://127.0.0.1:1455/auth/callback'}</code>, which only works in a browser on the computer running Adam. On another device (for example over Tailscale), the tab will show a “can’t connect” page after you approve. Copy the full address from that tab and paste it here.</p>
      <div className="key-row"><label className="field-label">Callback URL<input type="text" value={callbackUrl} onChange={(event) => setCallbackUrl(event.target.value)} autoComplete="off" spellCheck={false} placeholder="http://127.0.0.1:1455/auth/callback?code=…&state=…" /></label><button type="button" className="secondary-button" disabled={!callbackUrl.trim() || busy} onClick={() => void finishWithUrl()}>Finish sign-in</button></div>
    </details>}
    {message && <p className="chatgpt-message" role="status">{message}</p>}
  </div>;
}

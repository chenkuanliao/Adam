'use client';

import { useId, useRef, useState } from 'react';
import { parseControlNumber } from './reader-controls';

/** Click to edit, Enter to apply, Escape/blur to discard. Invalid entries keep their focus. */
export default function EditableNumber({ value, min, max, onChange, label, suffix = '', integer = false, disabled = false }: {
  value: number; min: number; max: number; onChange: (value: number) => void; label: string; suffix?: string; integer?: boolean; disabled?: boolean;
}) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState('');
  const [error, setError] = useState('');
  const id = useId();
  const trigger = useRef<HTMLButtonElement>(null);
  function finish() { setEditing(false); setError(''); requestAnimationFrame(() => trigger.current?.focus({ preventScroll: true })); }
  return <span className="editable-number">
    {editing ? <input autoFocus className="number-editor" aria-label={label} aria-invalid={Boolean(error)} aria-describedby={error ? id : undefined} inputMode={integer ? 'numeric' : 'decimal'} value={draft} onFocus={(event) => event.currentTarget.select()} onChange={(event) => { setDraft(event.target.value); setError(''); }} onBlur={() => { setEditing(false); setError(''); }} onKeyDown={(event) => {
      if (event.key === 'Escape') { event.preventDefault(); event.stopPropagation(); finish(); }
      if (event.key !== 'Enter') return;
      event.preventDefault(); event.stopPropagation();
      const next = parseControlNumber(draft, min, max, integer);
      if (next === null) { setError(`Enter ${integer ? 'a whole number' : 'a number'} from ${min} to ${max}${suffix}.`); return; }
      onChange(next); finish();
    }} /> : <button ref={trigger} type="button" className="number-value" disabled={disabled} aria-label={`${label}: ${value}${suffix}. Click to edit`} data-tooltip={`Click to enter ${label.toLowerCase()} · ${min}–${max}${suffix}`} onClick={() => { setDraft(String(value)); setEditing(true); }}>{value}{suffix}</button>}
    {error && <span id={id} className="number-error" role="alert">{error}</span>}
  </span>;
}

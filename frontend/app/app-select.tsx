'use client';

import { useEffect, useId, useLayoutEffect, useRef, useState, type KeyboardEvent } from 'react';

type Option = { value: string; label: string; disabled?: boolean };

/** A keyboard-accessible select whose menu uses the app theme, including in dialogs. */
export default function AppSelect({ value, onChange, options, label, disabled, autoFocus }: {
  value: string; onChange: (value: string) => void; options: Option[]; label: string; disabled?: boolean; autoFocus?: boolean;
}) {
  const id = useId();
  const trigger = useRef<HTMLButtonElement>(null);
  const menu = useRef<HTMLDivElement>(null);
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState(0);
  const search = useRef({ text: '', at: 0 });
  const selected = options.findIndex((option) => option.value === value);

  function close() { setOpen(false); }
  function show() { setActive(Math.max(0, selected)); setOpen(true); }
  function choose(index: number) {
    if (!options[index] || options[index].disabled) return;
    onChange(options[index].value);
    close();
    trigger.current?.focus();
  }

  useLayoutEffect(() => {
    if (!open || !trigger.current || !menu.current) return;
    const element = menu.current;
    element.showPopover();
    function position() {
      const rect = trigger.current!.getBoundingClientRect();
      const height = Math.min(280, element.scrollHeight + 2, window.innerHeight - 24);
      const width = Math.min(Math.max(rect.width, 190), window.innerWidth - 24);
      element.style.width = `${width}px`;
      element.style.maxHeight = `${height}px`;
      element.style.left = `${Math.max(12, Math.min(rect.left, window.innerWidth - width - 12))}px`;
      element.style.top = `${Math.max(12, rect.bottom + height + 6 > window.innerHeight ? rect.top - height - 6 : rect.bottom + 6)}px`;
    }
    position();
    const outside = (event: PointerEvent) => {
      if (!element.contains(event.target as Node) && !trigger.current?.contains(event.target as Node)) close();
    };
    const scroll = (event: Event) => { if (!element.contains(event.target as Node)) close(); };
    document.addEventListener('pointerdown', outside);
    window.addEventListener('resize', position);
    document.addEventListener('scroll', scroll, true);
    return () => {
      if (element.matches(':popover-open')) element.hidePopover();
      document.removeEventListener('pointerdown', outside);
      window.removeEventListener('resize', position);
      document.removeEventListener('scroll', scroll, true);
    };
  }, [open]);

  useEffect(() => { if (open) menu.current?.children[active]?.scrollIntoView({ block: 'nearest' }); }, [open, active]);

  function keyDown(event: KeyboardEvent<HTMLButtonElement>) {
    if (event.key === 'Tab') { close(); return; }
    if (event.key === 'Escape' && open) { event.preventDefault(); event.stopPropagation(); close(); return; }
    if (['Enter', ' '].includes(event.key)) { event.preventDefault(); if (open) choose(active); else show(); return; }
    const enabled = options.map((option, index) => option.disabled ? -1 : index).filter((index) => index >= 0);
    if (['ArrowDown', 'ArrowUp', 'Home', 'End'].includes(event.key)) {
      event.preventDefault();
      if (!open) { show(); return; }
      const current = enabled.indexOf(active);
      const next = event.key === 'Home' ? enabled[0] : event.key === 'End' ? enabled.at(-1) : enabled[(current + (event.key === 'ArrowDown' ? 1 : -1) + enabled.length) % enabled.length];
      if (next !== undefined) setActive(next);
    } else if (event.key.length === 1 && !event.metaKey && !event.ctrlKey && !event.altKey) {
      event.preventDefault();
      search.current = { text: Date.now() - search.current.at < 700 ? search.current.text + event.key : event.key, at: Date.now() };
      if (!open) show();
      const index = options.findIndex((option) => !option.disabled && option.label.toLowerCase().startsWith(search.current.text.toLowerCase()));
      if (index >= 0) setActive(index);
    }
  }

  return <span className="app-select">
    <button type="button" className="app-select-trigger" role="combobox" aria-label={label} aria-expanded={open} aria-controls={id} aria-haspopup="listbox" aria-activedescendant={open ? `${id}-${active}` : undefined} ref={trigger} disabled={disabled} autoFocus={autoFocus} onClick={() => open ? close() : show()} onKeyDown={keyDown} onBlur={(event) => { if (!menu.current?.contains(event.relatedTarget)) close(); }}>
      <span>{options[selected]?.label ?? 'Choose…'}</span><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" aria-hidden="true"><path d="m6 9 6 6 6-6" /></svg>
    </button>
    {open && <div id={id} ref={menu} popover="manual" className="app-select-menu" onClick={(event) => { event.preventDefault(); event.stopPropagation(); }} role="listbox" aria-label={label}>{options.map((option, index) => <div id={`${id}-${index}`} key={option.value} role="option" aria-selected={value === option.value} aria-disabled={option.disabled || undefined} className={active === index ? 'is-active' : ''} onPointerMove={() => { if (!option.disabled) setActive(index); }} onMouseDown={(event) => event.preventDefault()} onClick={() => choose(index)}><span>{option.label}</span><span aria-hidden="true">{option.value === value ? '✓' : ''}</span></div>)}</div>}
  </span>;
}

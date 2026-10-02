'use client';

import { useEffect, useId, useState } from 'react';
import { createPortal } from 'react-dom';

type Hint = { text: string; x: number; y: number; above: boolean; container: HTMLElement };

/** One floating hint layer, so card clipping never hides button descriptions. */
export default function ActionTooltips() {
  const id = useId();
  const [hint, setHint] = useState<Hint | null>(null);
  useEffect(() => {
    let target: HTMLElement | null = null;
    let previousDescription: string | null = null;
    let timer: ReturnType<typeof setTimeout> | undefined;
    function hide() {
      clearTimeout(timer);
      if (target) {
        if (previousDescription === null) target.removeAttribute('aria-describedby');
        else target.setAttribute('aria-describedby', previousDescription);
      }
      target = null;
      setHint(null);
    }
    function show(event: Event) {
      const element = event.target instanceof Element ? event.target.closest<HTMLElement>('[data-tooltip]') : null;
      if (element === target) return;
      hide();
      if (!element || !element.dataset.tooltip) return;
      target = element;
      previousDescription = element.getAttribute('aria-describedby');
      timer = setTimeout(() => {
        if (!element.isConnected) return;
        const rect = element.getBoundingClientRect();
        const above = rect.bottom > window.innerHeight - 65;
        element.setAttribute('aria-describedby', [previousDescription, id].filter(Boolean).join(' '));
        setHint({ text: element.dataset.tooltip!, x: Math.max(140, Math.min(window.innerWidth - 140, rect.left + rect.width / 2)), y: above ? rect.top - 8 : rect.bottom + 8, above, container: element.closest('dialog') ?? document.body });
      }, 220);
    }
    function leave(event: Event) {
      if (event instanceof MouseEvent && event.relatedTarget instanceof Node && target?.contains(event.relatedTarget)) return;
      hide();
    }
    document.addEventListener('pointerover', show);
    document.addEventListener('focusin', show);
    document.addEventListener('pointerout', leave);
    document.addEventListener('focusout', hide);
    document.addEventListener('pointerdown', hide);
    document.addEventListener('keydown', hide);
    window.addEventListener('scroll', hide, true);
    return () => {
      clearTimeout(timer);
      if (target) {
        if (previousDescription === null) target.removeAttribute('aria-describedby');
        else target.setAttribute('aria-describedby', previousDescription);
      }
      document.removeEventListener('pointerover', show);
      document.removeEventListener('focusin', show);
      document.removeEventListener('pointerout', leave);
      document.removeEventListener('focusout', hide);
      document.removeEventListener('pointerdown', hide);
      document.removeEventListener('keydown', hide);
      window.removeEventListener('scroll', hide, true);
    };
  }, [id]);
  return hint ? createPortal(<div id={id} className={`action-tooltip${hint.above ? ' above' : ''}`} role="tooltip" style={{ left: hint.x, top: hint.y }}>{hint.text}</div>, hint.container) : null;
}

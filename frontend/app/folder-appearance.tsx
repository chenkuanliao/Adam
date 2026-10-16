'use client';

import { useState, type CSSProperties } from 'react';

export const DEFAULT_FOLDER_COLOR = '#8c9d65';
const PRESETS = [
  { name: 'Sage', color: DEFAULT_FOLDER_COLOR },
  { name: 'Gold', color: '#c49a47' },
  { name: 'Coral', color: '#c8795b' },
  { name: 'Rose', color: '#c46c8c' },
  { name: 'Lavender', color: '#9275bc' },
  { name: 'Blue', color: '#618fbd' },
  { name: 'Teal', color: '#549b95' },
  { name: 'Slate', color: '#839099' },
];

export function folderStyle(value: string = DEFAULT_FOLDER_COLOR): CSSProperties {
  const color = /^#[0-9a-f]{6}$/i.test(value) ? value.toLowerCase() : DEFAULT_FOLDER_COLOR;
  const rgb = [1, 3, 5].map((offset) => parseInt(color.slice(offset, offset + 2), 16));
  // Keep very light custom colors visible against the library's pale canvas.
  const luminance = rgb[0] * .299 + rgb[1] * .587 + rgb[2] * .114;
  const ink = luminance > 185 ? '#' + rgb.map((channel) => Math.round(channel * .6).toString(16).padStart(2, '0')).join('') : color;
  return { '--folder-color': color, '--folder-ink': ink, '--folder-tint': `${color}1a` } as CSSProperties;
}

export default function FolderColorPicker({ value, onChange, disabled }: { value: string; onChange: (color: string) => void; disabled: boolean }) {
  const selectedName = PRESETS.find((preset) => preset.color === value)?.name ?? 'Custom';
  return <fieldset className="folder-color-picker" disabled={disabled}>
    <legend>Folder color</legend>
    <div className="folder-color-swatches" role="group" aria-label="Default folder colors">
      {PRESETS.map((preset) => <button key={preset.name} type="button" aria-label={`${preset.name} folder color`} aria-pressed={value === preset.color} data-tooltip={preset.name} className="folder-color-swatch" style={{ backgroundColor: preset.color }} onClick={() => onChange(preset.color)}>{value === preset.color && <svg viewBox="0 0 24 24" width="17" height="17" fill="none" stroke="white" strokeWidth="2.5" aria-hidden="true"><path d="m5 12 4 4L19 6" /></svg>}</button>)}
    </div>
    <div className="folder-custom-color"><label className="folder-custom-color-control"><span className="custom-color-swatch" style={{ background: value }} aria-hidden="true" /><CustomColorInput key={value} value={value} onChange={onChange} /><span>Custom color</span></label><output aria-live="polite">{selectedName}<span>{value.toUpperCase()}</span></output></div>
  </fieldset>;
}

function CustomColorInput({ value, onChange }: { value: string; onChange: (color: string) => void }) {
  const [draft, setDraft] = useState(value);
  const valid = /^#[0-9a-f]{6}$/i.test(draft);
  return <input type="text" aria-label="Custom folder color" className="custom-color-hex" aria-invalid={!valid} value={draft} maxLength={7} spellCheck={false} placeholder="#618fbd" onChange={(event) => setDraft(event.target.value)} onBlur={() => { if (valid) onChange(draft.toLowerCase()); else setDraft(value); }} onKeyDown={(event) => { if (event.key === 'Enter') { event.preventDefault(); if (valid) onChange(draft.toLowerCase()); } }} />;
}

export const FOLDER_ICONS = [
  { id: 'folder', name: 'Folder', path: 'M3 7V5a2 2 0 0 1 2-2h5l2 3h7a2 2 0 0 1 2 2v11a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V7Z' },
  { id: 'book', name: 'Book', path: 'M12 5v16M12 5C9 3 5 3 2 4v15c3-1 7-1 10 2 3-3 7-3 10-2V4c-3-1-7-1-10 1Z' },
  { id: 'bookmark', name: 'Bookmark', path: 'M6 3h12v18l-6-4-6 4V3Z' },
  { id: 'star', name: 'Star', path: 'm12 3 2.8 5.7 6.2.9-4.5 4.4 1.1 6.2-5.6-3-5.6 3 1.1-6.2L3 9.6l6.2-.9L12 3Z' },
  { id: 'heart', name: 'Heart', path: 'M20.8 4.6a5.5 5.5 0 0 0-7.8 0l-1 1-1-1a5.5 5.5 0 0 0-7.8 7.8L12 21l8.8-8.6a5.5 5.5 0 0 0 0-7.8Z' },
  { id: 'lightbulb', name: 'Lightbulb', path: 'M9 18h6M9 21h6M8 15a7 7 0 1 1 8 0l-1 3H9l-1-3Z' },
  { id: 'flask', name: 'Flask', path: 'M9 3h6M10 3v6L4 19a1.3 1.3 0 0 0 1 2h14a1.3 1.3 0 0 0 1-2L14 9V3M7 15h10' },
  { id: 'code', name: 'Code', path: 'm8 6-6 6 6 6m8-12 6 6-6 6M14 3l-4 18' },
  { id: 'globe', name: 'Globe', path: 'M21 12a9 9 0 1 1-18 0 9 9 0 0 1 18 0ZM3 12h18M12 3c5 5 5 13 0 18-5-5-5-13 0-18Z' },
  { id: 'leaf', name: 'Leaf', path: 'M20 3C9 2 3 7 5 15s17 7 15-12ZM3 21 15 9' },
  { id: 'briefcase', name: 'Briefcase', path: 'M8 7V3h8v4M3 7h18v14H3V7ZM3 12c5 4 13 4 18 0M10 14h4' },
  { id: 'graduation', name: 'Graduation cap', path: 'm2 9 10-5 10 5-10 5L2 9Zm4 2v6c4 3 8 3 12 0v-6M22 9v8' },
] as const;
export type FolderIconName = typeof FOLDER_ICONS[number]['id'];
export const DEFAULT_FOLDER_ICON: FolderIconName = 'folder';

export function FolderIcon({ value = DEFAULT_FOLDER_ICON }: { value?: string }) {
  const icon = FOLDER_ICONS.find((item) => item.id === value) ?? FOLDER_ICONS[0];
  return <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d={icon.path} /></svg>;
}

export function FolderIconPicker({ value, color, onChange, disabled }: { value: FolderIconName; color: string; onChange: (icon: FolderIconName) => void; disabled: boolean }) {
  return <fieldset className="folder-icon-picker" disabled={disabled} style={folderStyle(color)}>
    <legend>Folder icon</legend>
    <div className="folder-icon-options" role="group" aria-label="Folder icons">
      {FOLDER_ICONS.map((icon) => <button key={icon.id} type="button" className="folder-icon-option" aria-label={`${icon.name} folder icon`} aria-pressed={value === icon.id} data-tooltip={icon.name} onClick={() => onChange(icon.id)}><FolderIcon value={icon.id} /></button>)}
    </div>
  </fieldset>;
}

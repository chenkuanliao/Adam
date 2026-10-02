'use client';

import type { CSSProperties } from 'react';

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
    <div className="folder-custom-color"><label className="folder-custom-color-control"><input type="color" aria-label="Custom folder color" data-tooltip="Choose any custom folder color" value={value} onChange={(event) => onChange(event.target.value.toLowerCase())} /><span>Custom color</span></label><output aria-live="polite">{selectedName}<span>{value.toUpperCase()}</span></output></div>
  </fieldset>;
}

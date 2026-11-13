'use client';

import { useState } from 'react';
import { readThemePreference, setThemePreference, type ThemePreference } from './theme';

const OPTIONS: { value: ThemePreference; label: string; hint: string }[] = [
  { value: 'light', label: 'Light', hint: 'Ivory paper' },
  { value: 'dark', label: 'Dark', hint: 'Low light' },
  { value: 'system', label: 'System', hint: 'Match device' },
];

/** Settings → Appearance: pick light, dark, or follow the operating system. Applies instantly. */
export default function AppearanceSettings() {
  // Only mounted inside the Settings dialog, which never renders on the server.
  const [theme, setTheme] = useState<ThemePreference>(readThemePreference);

  function choose(next: ThemePreference) {
    setTheme(next);
    setThemePreference(next);
  }

  return <div className="appearance-page">
    <div className="provider-heading"><div><h3>Appearance</h3><p>Choose how Adam looks. Changes apply right away and are saved in this browser.</p></div></div>
    <div className="theme-options" role="radiogroup" aria-label="Theme">
      {OPTIONS.map((option) => <button key={option.value} type="button" role="radio" aria-checked={theme === option.value} className="theme-option" onClick={() => choose(option.value)}>
        <span className={`theme-swatch theme-swatch-${option.value}`} aria-hidden="true">
          {option.value !== 'dark' && <span className="theme-swatch-half theme-swatch-light" />}
          {option.value !== 'light' && <span className="theme-swatch-half theme-swatch-dark" />}
        </span>
        <span>{option.label}<small>{theme === option.value ? 'Selected' : option.hint}</small></span>
      </button>)}
    </div>
    <p className="appearance-note">PDF pages always stay on white paper so figures and highlights read as printed. System follows your device&apos;s light or dark setting as it changes.</p>
  </div>;
}

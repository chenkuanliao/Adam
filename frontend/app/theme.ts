// Light / dark / system appearance. The choice is per browser (localStorage), like the reader's
// zoom and pane widths, and is applied as <html data-theme="light|dark"> before first paint.

export type ThemePreference = 'light' | 'dark' | 'system';
export type ResolvedTheme = 'light' | 'dark';

export const THEME_STORAGE_KEY = 'adam-theme';
const DARK_QUERY = '(prefers-color-scheme: dark)';

function isPreference(value: unknown): value is ThemePreference {
  return value === 'light' || value === 'dark' || value === 'system';
}

export function readThemePreference(): ThemePreference {
  try {
    const value = window.localStorage.getItem(THEME_STORAGE_KEY);
    return isPreference(value) ? value : 'system';
  } catch {
    return 'system';
  }
}

export function resolveTheme(preference: ThemePreference): ResolvedTheme {
  if (preference !== 'system') return preference;
  return window.matchMedia?.(DARK_QUERY).matches ? 'dark' : 'light';
}

export function applyTheme(preference: ThemePreference) {
  const root = document.documentElement;
  const theme = resolveTheme(preference);
  root.dataset.theme = theme;
  root.dataset.themePreference = preference;
  root.style.colorScheme = theme;
}

export function setThemePreference(preference: ThemePreference) {
  try { window.localStorage.setItem(THEME_STORAGE_KEY, preference); } catch { /* private mode: still apply for this page */ }
  applyTheme(preference);
}

// Runs inline in <head> so the page never flashes the wrong theme, and keeps "system" in sync
// with the OS setting while the app is open. Kept dependency-free: it is serialized as a string.
export const THEME_BOOT_SCRIPT = `(function(){var k=${JSON.stringify(THEME_STORAGE_KEY)},q=${JSON.stringify(DARK_QUERY)};function p(){try{var v=localStorage.getItem(k);return v==='light'||v==='dark'||v==='system'?v:'system'}catch(e){return'system'}}function a(){var v=p(),m=window.matchMedia&&window.matchMedia(q).matches,t=v==='system'?(m?'dark':'light'):v,r=document.documentElement;r.dataset.theme=t;r.dataset.themePreference=v;r.style.colorScheme=t}a();try{window.matchMedia(q).addEventListener('change',a)}catch(e){}window.addEventListener('storage',function(e){if(e.key===k)a()})})();`;

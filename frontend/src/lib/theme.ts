import { useEffect, useState } from 'react';

export type ThemeMode = 'light' | 'dark' | 'system';

/* The colour scheme (globals.css): Měď is the default and needs no attribute;
 * the other two set `data-scheme` on <html>. Light/dark is the separate
 * ThemeMode, so every scheme has both sides. */
export type ColorScheme = 'med' | 'kobalt' | 'grafit';

export const COLOR_SCHEMES: ReadonlyArray<{
  value: ColorScheme;
  label: string;
  /* Canvas, accent, live price — drawn as the picker's swatch (light side). */
  swatch: readonly [string, string, string];
}> = [
  { value: 'med', label: 'Měď', swatch: ['#f2efe7', '#1f6b5c', '#38692a'] },
  { value: 'kobalt', label: 'Kobalt', swatch: ['#eef1f5', '#1f45b5', '#1f7346'] },
  { value: 'grafit', label: 'Grafit', swatch: ['#f0f0ee', '#18181b', '#1b7548'] },
];

const STORAGE_KEY = 'sreality.theme';
const SCHEME_KEY = 'sreality.scheme';

export function readStoredTheme(): ThemeMode {
  try {
    const v = localStorage.getItem(STORAGE_KEY);
    if (v === 'light' || v === 'dark' || v === 'system') return v;
  } catch {
    /* localStorage may be unavailable (SSR, private mode lockdown) — fall through */
  }
  return 'light';
}

export function writeStoredTheme(mode: ThemeMode): void {
  try {
    localStorage.setItem(STORAGE_KEY, mode);
  } catch {
    /* ignore */
  }
}

export function applyTheme(mode: ThemeMode): void {
  const root = document.documentElement;
  if (mode === 'system') root.removeAttribute('data-theme');
  else root.setAttribute('data-theme', mode);
}

export function useTheme(): [ThemeMode, (mode: ThemeMode) => void] {
  const [mode, setMode] = useState<ThemeMode>(() => readStoredTheme());

  useEffect(() => {
    applyTheme(mode);
    writeStoredTheme(mode);
  }, [mode]);

  return [mode, setMode];
}

export function readStoredScheme(): ColorScheme {
  try {
    const v = localStorage.getItem(SCHEME_KEY);
    if (v === 'med' || v === 'kobalt' || v === 'grafit') return v;
  } catch {
    /* same as readStoredTheme */
  }
  return 'med';
}

export function writeStoredScheme(scheme: ColorScheme): void {
  try {
    localStorage.setItem(SCHEME_KEY, scheme);
  } catch {
    /* ignore */
  }
}

export function applyScheme(scheme: ColorScheme): void {
  const root = document.documentElement;
  if (scheme === 'med') root.removeAttribute('data-scheme');
  else root.setAttribute('data-scheme', scheme);
}

export function useScheme(): [ColorScheme, (scheme: ColorScheme) => void] {
  const [scheme, setScheme] = useState<ColorScheme>(() => readStoredScheme());

  useEffect(() => {
    applyScheme(scheme);
    writeStoredScheme(scheme);
  }, [scheme]);

  return [scheme, setScheme];
}

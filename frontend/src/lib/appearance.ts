/* The appearance's values and its one paint rule, shared by the app's store
 * (lib/theme.ts) and the Chrome extension, which reads the same profile value
 * from its session token. No imports, no React, no DOM at import. */
export type ThemeMode = 'light' | 'dark' | 'system';
export type ColorScheme = 'med' | 'kobalt' | 'grafit';
export interface Appearance {
  scheme: ColorScheme;
  theme: ThemeMode;
}
/* As the profile keeps it: a pick with its time. */
export interface SavedAppearance extends Appearance {
  at: string;
}

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

/* Light or dark, in picker order; a surface in another language relabels it. */
export const THEME_MODES: ReadonlyArray<{ value: ThemeMode; label: string }> = [
  { value: 'light', label: 'Light' },
  { value: 'dark', label: 'Dark' },
  { value: 'system', label: 'System' },
];

export const SCHEMES = COLOR_SCHEMES.map((s) => s.value);
export const MODES = THEME_MODES.map((m) => m.value);
/* What a surface shows before it knows the user's pick. */
export const DEFAULT_APPEARANCE: Appearance = { scheme: 'med', theme: 'light' };

export const isTime = (at: unknown): at is string => typeof at === 'string' && !Number.isNaN(Date.parse(at));

/* The profile's `user_metadata.appearance`. Metadata is client-writable: only
 * the store's own ids and a real time get through. */
export function parseAppearance(value: unknown): SavedAppearance | null {
  if (typeof value !== 'object' || value === null) return null;
  const { scheme, theme, at } = value as Record<string, unknown>;
  if (!SCHEMES.includes(scheme as ColorScheme) || !MODES.includes(theme as ThemeMode) || !isTime(at)) return null;
  return { scheme: scheme as ColorScheme, theme: theme as ThemeMode, at };
}

/* `paintAppearance`'s systemDark: the OS side, and `onFlip` on every OS flip
 * (the listener attaches the first time it is asked). */
export function followSystemDark(onFlip: () => void): () => boolean {
  let query: MediaQueryList | null = null;
  return () => {
    if (query == null && typeof window.matchMedia === 'function') {
      query = window.matchMedia('(prefers-color-scheme: dark)');
      query.addEventListener('change', onFlip);
    }
    return query?.matches ?? false;
  };
}

/* THE paint rule (styles/tokens.css): Měď carries no `data-scheme`, and
 * `data-theme` is always light or dark — `system` asks `systemDark`, and only then. */
export function paintAppearance(el: Element, { scheme, theme }: Appearance, systemDark: () => boolean): void {
  if (scheme === 'med') el.removeAttribute('data-scheme');
  else el.setAttribute('data-scheme', scheme);
  const dark = theme === 'dark' || (theme === 'system' && systemDark());
  el.setAttribute('data-theme', dark ? 'dark' : 'light');
}

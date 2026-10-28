import { useSyncExternalStore } from 'react';
import { readChoice, readValue, writeValue } from './persistedFlag';

/* The user's appearance: a colour scheme (styles/tokens.css: Měď needs no
 * attribute, Kobalt and Grafit set `data-scheme` on <html>) and a light/dark
 * side, so every scheme has both. ONE store for every picker (account menu,
 * phone account sheet, Settings), cached on the device (index.html applies the
 * cache before the first paint) and kept in the signed-in user's profile. */
export type ThemeMode = 'light' | 'dark' | 'system';
export type ColorScheme = 'med' | 'kobalt' | 'grafit';
export interface Appearance {
  scheme: ColorScheme;
  theme: ThemeMode;
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

const SCHEMES = COLOR_SCHEMES.map((s) => s.value);
const MODES = THEME_MODES.map((m) => m.value);
const SCHEME_KEY = 'sreality.scheme';
const THEME_KEY = 'sreality.theme';
/* The user id whose change the profile has not confirmed yet ('' = none). */
const UNSAVED_KEY = 'sreality.appearance.unsaved';
const DEFAULT_APPEARANCE: Appearance = { scheme: 'med', theme: 'light' };

let state: Appearance = {
  scheme: readChoice(SCHEME_KEY, SCHEMES, DEFAULT_APPEARANCE.scheme),
  theme: readChoice(THEME_KEY, MODES, DEFAULT_APPEARANCE.theme),
};
const listeners = new Set<() => void>();
let systemQuery: MediaQueryList | null = null;

/* "system" is resolved here, so <html> always carries light or dark; an OS
 * flip re-applies it (the listener attaches the first time it is asked). */
function systemIsDark(): boolean {
  if (!systemQuery && typeof window.matchMedia === 'function') {
    systemQuery = window.matchMedia('(prefers-color-scheme: dark)');
    systemQuery.addEventListener('change', () => {
      if (state.theme === 'system') applyAppearance();
    });
  }
  return systemQuery?.matches ?? false;
}

export function applyAppearance(): void {
  const root = document.documentElement;
  if (state.scheme === 'med') root.removeAttribute('data-scheme');
  else root.setAttribute('data-scheme', state.scheme);
  const dark = state.theme === 'dark' || (state.theme === 'system' && systemIsDark());
  root.setAttribute('data-theme', dark ? 'dark' : 'light');
}

function commit(next: Appearance): boolean {
  if (next.scheme === state.scheme && next.theme === state.theme) return false;
  state = next;
  writeValue(SCHEME_KEY, next.scheme);
  writeValue(THEME_KEY, next.theme);
  applyAppearance();
  for (const listener of listeners) listener();
  return true;
}

/* The signed-in user's profile (Supabase Auth user_metadata.appearance; no
 * table, no route). `unsaved` = a choice made while signed in that the profile
 * has not confirmed: it beats the profile's older value and goes again with
 * every session event until a write lands. Its marker sits beside the device
 * cache, keyed to the user, so a reload keeps it. updateUser holds Supabase's
 * auth lock, so writes land in the order they were sent. */
type SaveAppearance = (appearance: Appearance) => Promise<void>;
let profile: { id: string; save: SaveAppearance } | null = null;
let unsaved = false;
let sending: Appearance | null = null;

function markUnsaved(id: string): void {
  unsaved = true;
  writeValue(UNSAVED_KEY, id);
}

function sendToProfile(): void {
  if (!profile || sending === state) return;
  const { id, save } = profile;
  const sent = state;
  sending = sent;
  const settle = (saved: boolean) => {
    if (sending === sent) sending = null;
    if (saved && state === sent && profile?.id === id) {
      unsaved = false;
      writeValue(UNSAVED_KEY, '');
    }
  };
  save(sent).then(() => settle(true), () => settle(false));
}

function choose(next: Appearance): void {
  if (!commit(next) || !profile) return;
  markUnsaved(profile.id);
  sendToProfile();
}

export function setScheme(scheme: ColorScheme): void {
  choose({ ...state, scheme });
}

export function setTheme(theme: ThemeMode): void {
  choose({ ...state, theme });
}

/* Metadata is client-writable: only the store's own ids get through. */
function parseAppearance(value: unknown): Appearance | null {
  if (typeof value !== 'object' || value === null) return null;
  const { scheme, theme } = value as Record<string, unknown>;
  if (!SCHEMES.includes(scheme as ColorScheme) || !MODES.includes(theme as ThemeMode)) return null;
  return { scheme: scheme as ColorScheme, theme: theme as ThemeMode };
}

/* Fed every auth session (lib/auth.tsx), which is the copy Supabase keeps on
 * the device: another device's change arrives with the next token refresh (up
 * to an hour). The profile's value wins and is cached here, unless this device
 * still owes it a change. A profile that holds none gets this device's choice
 * once per sign-in, and only when it differs from the default, so a device that
 * never chose cannot overwrite another's choice. A value this build does not
 * know is left alone. */
export function syncAppearance(
  user: { id: string; user_metadata?: { appearance?: unknown } } | null,
  save: SaveAppearance,
): void {
  const known = user !== null && profile?.id === user.id;
  profile = user && { id: user.id, save };
  if (!known) {
    const owedTo = readValue(UNSAVED_KEY, '');
    unsaved = user !== null && owedTo === user.id;
    /* Another user's owed change ends here: it never holds back this user's profile. */
    if (user && owedTo && !unsaved) writeValue(UNSAVED_KEY, '');
  }
  if (!user) return;
  if (unsaved) {
    sendToProfile();
    return;
  }
  const stored = user.user_metadata?.appearance;
  const remote = parseAppearance(stored);
  if (remote) commit(remote);
  else if (
    stored == null &&
    !known &&
    (state.scheme !== DEFAULT_APPEARANCE.scheme || state.theme !== DEFAULT_APPEARANCE.theme)
  ) {
    markUnsaved(user.id);
    sendToProfile();
  }
}

function subscribe(listener: () => void): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

export function useTheme(): [ThemeMode, (theme: ThemeMode) => void] {
  return [useSyncExternalStore(subscribe, () => state.theme), setTheme];
}

export function useScheme(): [ColorScheme, (scheme: ColorScheme) => void] {
  return [useSyncExternalStore(subscribe, () => state.scheme), setScheme];
}

import { useSyncExternalStore } from 'react';
import {
  DEFAULT_APPEARANCE,
  MODES,
  SCHEMES,
  followSystemDark,
  isTime,
  paintAppearance,
  parseAppearance,
  type Appearance,
  type ColorScheme,
  type SavedAppearance,
  type ThemeMode,
} from './appearance';
import { readChoice, readValue, writeValue } from './persistedFlag';

/* The user's appearance: a colour scheme (styles/tokens.css: Měď needs no
 * attribute, Kobalt and Grafit set `data-scheme` on <html>) and a light/dark
 * side, so every scheme has both. ONE store for every picker (account menu,
 * phone account sheet, Settings), cached on the device (index.html applies the
 * cache before the first paint) and kept in the signed-in user's profile. */
const SCHEME_KEY = 'sreality.scheme';
const THEME_KEY = 'sreality.theme';
/* Beside the two values index.html reads: when the cached appearance was picked
 * and, if signed in, by whom ("<ISO time>" or "<ISO time> <user id>"). */
const CHOSEN_KEY = 'sreality.appearance.chosen';

/* On the device, `user` names whose pick it is (absent when picked signed out);
 * a default, or a cache written before picks were timed, has no `at`, so it
 * never overwrites a profile. */
type Cached = Appearance & { at?: string; user?: string };
const [cachedAt, cachedUser] = readValue(CHOSEN_KEY, '').split(' ');
let state: Cached = {
  scheme: readChoice(SCHEME_KEY, SCHEMES, DEFAULT_APPEARANCE.scheme),
  theme: readChoice(THEME_KEY, MODES, DEFAULT_APPEARANCE.theme),
  at: isTime(cachedAt) ? cachedAt : undefined,
  user: cachedUser || undefined,
};
const listeners = new Set<() => void>();

/* "system" is resolved here, so <html> always carries light or dark; an OS
 * flip re-applies it. */
const systemIsDark = followSystemDark(() => {
  if (state.theme === 'system') applyAppearance();
});

export function applyAppearance(): void {
  paintAppearance(document.documentElement, state, systemIsDark);
}

function commit(next: SavedAppearance & { user?: string }): void {
  state = next;
  writeValue(SCHEME_KEY, next.scheme);
  writeValue(THEME_KEY, next.theme);
  writeValue(CHOSEN_KEY, next.user ? `${next.at} ${next.user}` : next.at);
  applyAppearance();
  for (const listener of listeners) listener();
}

/* lib/auth.tsx's two calls on the signed-in user's Supabase Auth profile
 * (user_metadata.appearance; no table, no route). */
type ProfileUser = { id: string; user_metadata?: { appearance?: unknown } };
export interface AppearanceProfile {
  read: () => Promise<ProfileUser | null>;
  save: (appearance: SavedAppearance) => Promise<unknown>;
}
let signedIn: { id: string; profile: AppearanceProfile } | null = null;
let firstRead: Promise<void> | null = null;

/* A failed write is not tracked: the pick stays newer than the profile, so the
 * next session sends it again. */
function send(): void {
  if (signedIn && state.at) void signedIn.profile.save({ scheme: state.scheme, theme: state.theme, at: state.at });
}

function choose(change: Partial<Appearance>): void {
  commit({ ...state, ...change, at: new Date().toISOString(), user: signedIn?.id });
  send();
}

export function setScheme(scheme: ColorScheme): void {
  choose({ scheme });
}

export function setTheme(theme: ThemeMode): void {
  choose({ theme });
}

/* THE rule: the newest explicit pick wins. The profile's is applied and cached
 * (with its own time, as this user's) unless this device holds a newer pick by
 * the same user, which goes to the profile instead. */
function reconcile(user: ProfileUser | null): void {
  if (!user || user.id !== signedIn?.id) return;
  const saved = parseAppearance(user.user_metadata?.appearance);
  const mine = state.user === user.id ? state.at : undefined;
  if (saved && !(mine && Date.parse(mine) > Date.parse(saved.at))) commit({ ...saved, user: user.id });
  else if (mine) send();
}

/* Fed every auth session (lib/auth.tsx). A session's user is the copy kept on
 * the device, up to an hour old (the next token refresh), so a page load's first
 * signed-in sync reads the profile from Auth instead, and later ones wait for
 * that read, so no copy is weighed before it. */
export function syncAppearance(user: ProfileUser | null, profile: AppearanceProfile): void {
  signedIn = user && { id: user.id, profile };
  if (!user) return;
  if (firstRead) void firstRead.then(() => reconcile(user));
  else firstRead = profile.read().then(reconcile, () => {});
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

/* THE switch between the two shells (the Phone app map, #1759 / W1 #1766).
 *
 * One SPA, one address, one deploy (#1760 Q4): the phone shell is not a second
 * app, it is a second FRAME around the same routes, chosen by screen width
 * (Q20). Under 768 px the shell draws a phone header and a bottom tab bar; every
 * route still renders its own page inside it, so a surface the phone has not
 * been designed for yet is cramped but reachable, never missing.
 *
 * The "desktop view" override is per device (localStorage), not per account: the
 * operator who wants the full desktop layout on his phone wants it on THAT
 * phone. Flipping it reloads that page; another tab's flip reaches this one
 * through the `storage` event, so every frame reader switches at once, as on a
 * rotation (the width is live: an iPad turned across the breakpoint switches
 * frames). ONE definition of the frame, `isPhoneFrame()`: the hook renders it,
 * the curation clock's cadence reads it outside React. */

import { useSyncExternalStore } from 'react';

import { ROUTES, type RoutePath } from './routes';

/* The breakpoint: Tailwind's `md` minus one, so a phone in landscape (≤ 767 px
 * wide on every current iPhone except the Max models) keeps the phone frame. */
export const PHONE_SHELL_QUERY = '(max-width: 767px)';

/* The per-device override: '1' = draw the desktop shell even on a phone. */
export const DESKTOP_VIEW_KEY = 'sreality.phone.desktopView';

export function readDesktopView(): boolean {
  try {
    return localStorage.getItem(DESKTOP_VIEW_KEY) === '1';
  } catch {
    /* Storage can throw (private-mode lockdown): no override. */
    return false;
  }
}

export function writeDesktopView(on: boolean): void {
  try {
    if (on) localStorage.setItem(DESKTOP_VIEW_KEY, '1');
    else localStorage.removeItem(DESKTOP_VIEW_KEY);
  } catch {
    /* ignore */
  }
}

/* Flip the override and reload: both shells mount different providers' worth
 * of chrome, and a reload is the one switch that cannot leave half of either. */
export function switchDesktopView(on: boolean): void {
  writeDesktopView(on);
  window.location.reload();
}

const hasMatchMedia = (): boolean =>
  typeof window !== 'undefined' && typeof window.matchMedia === 'function';

/* The frame's two inputs: the width crossing the breakpoint, and the override
 * written in another tab (`key` null: that tab cleared the storage). */
const subscribeFrame = (onChange: () => void): (() => void) => {
  const onStorage = (e: StorageEvent): void => {
    if (e.key === DESKTOP_VIEW_KEY || e.key === null) onChange();
  };
  window.addEventListener('storage', onStorage);
  const mql = hasMatchMedia() ? window.matchMedia(PHONE_SHELL_QUERY) : null;
  mql?.addEventListener('change', onChange);
  return () => {
    window.removeEventListener('storage', onStorage);
    mql?.removeEventListener('change', onChange);
  };
};

const isNarrow = (): boolean => hasMatchMedia() && window.matchMedia(PHONE_SHELL_QUERY).matches;

/** The frame, read now: a narrow screen and no desktop-view override. The curation
 * clock's cadence reads it outside React (lib/curationSyncOwner, ruling Q2). */
export const isPhoneFrame = (): boolean => isNarrow() && !readDesktopView();

/** The frame, rendered: `isPhoneFrame()`, re-read when the width crosses the breakpoint or another tab flips the override. */
export function usePhoneShell(): boolean {
  return useSyncExternalStore(subscribeFrame, isPhoneFrame, () => false);
}

/* The bar is 56 px plus the home-indicator inset, never less than 8 px of air
 * below the labels (#1763: `max(8px, env(safe-area-inset-bottom))`). The frame
 * pads <main> by the same sum, and the toasts sit just above it. */
export const TAB_BAR_INSET = 'max(8px, env(safe-area-inset-bottom))';
export const TAB_BAR_SPACE = `calc(56px + ${TAB_BAR_INSET})`;

/* The six areas of the bottom tab bar: the four of W1, Odkaz (paste a link,
 * W2 #1767) and Prohlídky (my viewings, migration 666). `owns` decides which
 * tab a path belongs to: a collection's own page is under Sbírky, a broker's
 * under Makléři. */
export type PhoneTabKey = 'feed' | 'pipeline' | 'viewings' | 'collections' | 'brokers' | 'link';

export interface PhoneTab {
  key: PhoneTabKey;
  label: string;
  to: RoutePath;
  owns: (pathname: string) => boolean;
}

const under = (pathname: string, root: string): boolean =>
  pathname === root || pathname.startsWith(`${root}/`);

export const PHONE_TABS: ReadonlyArray<PhoneTab> = [
  { key: 'feed', label: 'Nové', to: ROUTES.feed.build(), owns: (p) => under(p, ROUTES.feed.pattern) },
  { key: 'pipeline', label: 'Pipeline', to: ROUTES.pipeline.build(), owns: (p) => under(p, ROUTES.pipeline.pattern) },
  { key: 'viewings', label: 'Prohlídky', to: ROUTES.viewings.build(), owns: (p) => under(p, ROUTES.viewings.pattern) },
  {
    key: 'collections',
    label: 'Sbírky',
    to: ROUTES.collections.build(),
    owns: (p) => under(p, ROUTES.collections.pattern) || under(p, '/collection'),
  },
  { key: 'brokers', label: 'Makléři', to: ROUTES.brokers.build(), owns: (p) => under(p, ROUTES.brokers.pattern) },
  { key: 'link', label: 'Odkaz', to: ROUTES.link.build(), owns: (p) => under(p, ROUTES.link.pattern) },
];

export const phoneTabFor = (pathname: string): PhoneTab | null =>
  PHONE_TABS.find((t) => t.owns(pathname)) ?? null;

/* A tab's own root page draws no back affordance; everything else does. */
export const isPhoneRoot = (pathname: string): boolean =>
  PHONE_TABS.some((t) => t.to === pathname);

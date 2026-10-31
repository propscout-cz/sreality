/* Acting as a pipeline's owner (migration 641, docs/design/pipeline-sharing.md
 * §3.5): ONE selection, ONE context, ONE option, ONE key segment.
 *
 * - The ACTIVE BOARD is a per-device selection (localStorage, like the phone
 *   shell's desktop-view setting): null = "Můj pipeline", else one board shared
 *   with me. The Pipeline tab's switcher writes it; it survives reloads (§8-11).
 * - EDIT AS OWNER is the property page's sticky switch (§8-10): on, it stays on
 *   across property pages until "Ukončit" or until the active board changes,
 *   which clears it.
 * - `<ActingAs share>` is the context the boards mount on a shared board (the
 *   desktop Pipeline tab, the phone's stage pages), the property page while
 *   editing as the owner, and the new-property dialog opened from a shared
 *   board; `useActing()` answers the share or null.
 * - Inside it every curation and pipeline hook prefixes its query keys with
 *   `actingKey` and hands `actAs` to its transport (lib/api `request`, lib/pgRead
 *   `setHeader`), so the owner's rows never land in the caller's own caches.
 *   Outside a provider `actingKey` returns the key it was given — byte for byte
 *   today's — and `actOpts` adds no argument.
 *
 * Browse, the Collections page, Notifications and the extension never mount the
 * provider: a shared active board changes nothing there. */

import { createContext, createElement, useContext, useSyncExternalStore, type ReactNode } from 'react';
import type { QueryKey } from '@tanstack/react-query';

import type { PipelineShare, PipelineShareMode } from './types';

export const ACTIVE_BOARD_KEY = 'sreality.pipeline.activeBoard';
export const EDIT_AS_OWNER_KEY = 'sreality.pipeline.editAsOwner';

/* The first segment of every acting key; never the first segment of an own key. */
const ACTING_SEGMENT = 'acting';

export type ActiveBoard = PipelineShare | null;

/* ── The per-device store ─────────────────────────────────────────────────── */

let board: ActiveBoard | undefined;
let editAsOwner: boolean | undefined;
const listeners = new Set<() => void>();

const isMode = (v: unknown): v is PipelineShareMode => v === 'read' || v === 'edit';

function parseBoard(raw: string | null): ActiveBoard {
  if (raw == null) return null;
  try {
    const v: unknown = JSON.parse(raw);
    if (v == null || typeof v !== 'object') return null;
    const { account_id, email, mode } = v as Record<string, unknown>;
    if (typeof account_id !== 'string' || account_id === '' || !isMode(mode)) return null;
    if (email != null && typeof email !== 'string') return null;
    return { account_id, email: email ?? null, mode };
  } catch {
    return null;
  }
}

function readStorage(key: string): string | null {
  try {
    return localStorage.getItem(key);
  } catch {
    /* Storage can throw (private-mode lockdown): nothing remembered. */
    return null;
  }
}

function writeStorage(key: string, value: string | null): void {
  try {
    if (value == null) localStorage.removeItem(key);
    else localStorage.setItem(key, value);
  } catch {
    /* ignore */
  }
}

function emit(): void {
  for (const l of listeners) l();
}

export function getActiveBoard(): ActiveBoard {
  if (board === undefined) board = parseBoard(readStorage(ACTIVE_BOARD_KEY));
  return board;
}

function getEditFlag(): boolean {
  if (editAsOwner === undefined) editAsOwner = readStorage(EDIT_AS_OWNER_KEY) === '1';
  return editAsOwner;
}

const sameBoard = (a: ActiveBoard, b: ActiveBoard): boolean =>
  a === b ||
  (a != null && b != null && a.account_id === b.account_id && a.mode === b.mode && a.email === b.email);

/** Choose the active board (null = my own). Any change — another board, the
 * same board at another mode, a reset — ends "edit as owner". */
export function setActiveBoard(next: ActiveBoard): void {
  if (sameBoard(getActiveBoard(), next)) return;
  board = next;
  writeStorage(ACTIVE_BOARD_KEY, next == null ? null : JSON.stringify(next));
  editAsOwner = false;
  writeStorage(EDIT_AS_OWNER_KEY, null);
  emit();
}

/** Back to "Můj pipeline" (a revoked board, a leave, the switcher). */
export const resetActiveBoard = (): void => setActiveBoard(null);

/** Edit as the owner of the active board — only an EDIT board can be acted on. */
export function setEditAsOwner(on: boolean): void {
  const next = on && getActiveBoard()?.mode === 'edit';
  if (next === getEditFlag()) return;
  editAsOwner = next;
  writeStorage(EDIT_AS_OWNER_KEY, next ? '1' : null);
  emit();
}

/** Forget the cached values and re-read storage on demand — what another tab's
 * write does here, and what a page load does (tests use it as one). */
export function reloadActingStore(): void {
  board = undefined;
  editAsOwner = undefined;
  emit();
}

function onStorage(e: StorageEvent): void {
  if (e.key !== null && e.key !== ACTIVE_BOARD_KEY && e.key !== EDIT_AS_OWNER_KEY) return;
  reloadActingStore();
}

function subscribe(listener: () => void): () => void {
  listeners.add(listener);
  if (listeners.size === 1 && typeof window !== 'undefined') {
    window.addEventListener('storage', onStorage);
  }
  return () => {
    listeners.delete(listener);
    if (listeners.size === 0 && typeof window !== 'undefined') {
      window.removeEventListener('storage', onStorage);
    }
  };
}

const serverNull = (): null => null;
const editSnapshot = (): boolean => getEditFlag() && getActiveBoard()?.mode === 'edit';

/** The active board this device remembers (null = my own). */
export function useActiveBoard(): ActiveBoard {
  return useSyncExternalStore(subscribe, getActiveBoard, serverNull);
}

/** Is "edit as the owner" on (and the active board an edit share)? */
export function useEditAsOwner(): boolean {
  return useSyncExternalStore(subscribe, editSnapshot, () => false);
}

/** The property page's acting share (§8-4, §8-10): the active board while it
 * is an EDIT share and "edit as owner" is on; else null (act as myself). */
export function useEditAsOwnerShare(): PipelineShare | null {
  const active = useActiveBoard();
  const on = useEditAsOwner();
  return on && active?.mode === 'edit' ? active : null;
}

/* ── The context ──────────────────────────────────────────────────────────── */

const ActingContext = createContext<PipelineShare | null>(null);

/** Everything inside reads and writes curation as `share`'s owner; null = as myself. */
export function ActingAs({ share, children }: { share: PipelineShare | null; children: ReactNode }) {
  return createElement(ActingContext.Provider, { value: share }, children);
}

/** The share the surrounding `<ActingAs>` acts through, or null outside one. */
export function useActing(): PipelineShare | null {
  return useContext(ActingContext);
}

/** The owner account to send as `actAs`, or null. */
export function useActAs(): string | null {
  return useActing()?.account_id ?? null;
}

/** A read-only board: no drag, no stage menu, no trash, no tag chips. */
export function useActingReadOnly(): boolean {
  return useActing()?.mode === 'read';
}

/* ── The key segment and the option ───────────────────────────────────────── */

/** `key` itself outside a provider (today's key, byte for byte); under one,
 * `['acting', owner, ...key]` — a prefix no own key starts with, so an own
 * prefix invalidation never reaches an acting entry, nor the reverse. */
export function actingKey(actAs: string | null | undefined, key: QueryKey): QueryKey {
  return actAs ? [ACTING_SEGMENT, actAs, ...key] : key;
}

/** The trailing `{ actAs }` option of a lib/api wrapper, or no argument at all,
 * so a call outside a provider is exactly the call it always was. */
export const actOpts = (actAs: string | null | undefined): [] | [{ actAs: string }] =>
  actAs ? [{ actAs }] : [];

/* ── Labels ───────────────────────────────────────────────────────────────── */

export const SHARE_MODE_LABEL: Record<PipelineShareMode, string> = {
  read: 'jen ke čtení',
  edit: 'úpravy',
};

/** The owner's name for a board shared with me (its email). */
export const shareOwnerName = (share: PipelineShare): string => share.email ?? 'neznámý účet';

/** "petr@example.cz · jen ke čtení" — the switcher's and the viewer line's name for a board. */
export const sharedBoardLabel = (share: PipelineShare): string =>
  `${shareOwnerName(share)} · ${SHARE_MODE_LABEL[share.mode]}`;

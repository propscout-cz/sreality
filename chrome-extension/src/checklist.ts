/* THE save checklist, one builder for the listing panel's pickers (collections,
 * tags) and the search-page card's collection menu: a checkable row per item,
 * the state lines for a list not read yet or failed (never drawn as empty), the
 * last write's failure beside the rows, and the link to where the list is kept.
 * Each surface styles the same class names in its own stylesheet. */

import type { Collection } from '../../frontend/src/lib/types';
import { COLLECTIONS_URL } from './config';
import { h, setBusy } from './dom';
import { bellIconSvg, saveTargets } from './glyphs';

export interface Checklist<T> {
  /* Null until read: loading, or `failed`. */
  items: readonly T[] | null;
  failed: boolean;
  text: { loading: string; failed: string; empty: string };
  retry: () => void;
  key: (item: T) => string;
  member: (item: T) => boolean;
  /* A write of this list's kind is out: rows keep focus but take no click. */
  busy: boolean;
  label: (item: T) => Node[];
  toggle: (item: T) => void;
  error: string | null;
  manage: { href: string; text: string } | null;
}

/* A list's state in one line; the side panel draws a failed read as its failure lines ('err'). */
export function stateLine(text: string, retry?: () => void, cls = 'state'): HTMLElement {
  const p = h('p', cls, text);
  if (retry != null) {
    const again = h('button', 'link', 'Zkusit znovu');
    again.dataset.key = 'retry';
    again.onclick = retry;
    p.append(' ', again);
  }
  return p;
}

export function buildChecklist<T>(box: HTMLElement, c: Checklist<T>): void {
  if (c.items == null) {
    box.appendChild(c.failed ? stateLine(c.text.failed, c.retry) : stateLine(c.text.loading));
  } else if (c.items.length === 0) {
    box.appendChild(stateLine(c.text.empty));
  } else {
    const list = h('div', 'list');
    for (const item of c.items) {
      const member = c.member(item);
      const row = h('button', 'row' + (member ? ' row--member' : ''));
      row.setAttribute('aria-pressed', String(member));
      row.dataset.key = c.key(item);
      setBusy(row, c.busy);
      const check = h('span', 'check', '✓');
      check.setAttribute('aria-hidden', 'true');
      row.append(check, ...c.label(item));
      row.onclick = () => c.toggle(item);
      list.appendChild(row);
    }
    box.appendChild(list);
  }
  if (c.error != null) {
    const err = h('p', 'error', c.error);
    err.setAttribute('role', 'alert');
    box.appendChild(err);
  }
  if (c.manage != null) {
    const manage = h('a', 'link manage', c.manage.text);
    manage.href = c.manage.href;
    manage.target = '_blank';
    manage.rel = 'noopener';
    box.appendChild(manage);
  }
}

/* The app's <CollectionSaveMenu>: every collection of MINE (a shared one is never
 * a save target), monitored first and bell-marked. Creating, renaming and
 * monitoring stay in the app. */
export function buildCollectionChecklist(box: HTMLElement, o: {
  collections: Collection[] | null;
  failed: boolean;
  memberIds: ReadonlySet<number>;
  busy: boolean;
  error: string | null;
  retry: () => void;
  toggle: (collectionId: number) => void;
}): void {
  const targets = o.collections == null ? null : saveTargets(o.collections);
  buildChecklist<Collection>(box, {
    items: targets,
    failed: o.failed,
    text: {
      loading: 'Načítám kolekce…',
      failed: 'Kolekce se nepodařilo načíst.',
      empty: 'Zatím nemáte žádnou kolekci.',
    },
    retry: o.retry,
    key: (c) => `coll-${c.id}`,
    member: (c) => o.memberIds.has(c.id),
    busy: o.busy,
    label: (c) => {
      const name = h('span', 'name', c.name);
      name.title = c.name;
      if (!c.monitoring_enabled) return [name];
      const bell = h('span', 'bell');
      bell.title = 'Sledovaná — upozorní na změny';
      bell.innerHTML = bellIconSvg();
      return [name, bell];
    },
    toggle: (c) => o.toggle(c.id),
    error: o.error,
    manage: COLLECTIONS_URL == null ? null : {
      href: COLLECTIONS_URL,
      text: targets != null && targets.length === 0 ? 'Založit kolekci v aplikaci →' : 'Spravovat kolekce →',
    },
  });
}

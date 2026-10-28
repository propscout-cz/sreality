/* The side sheet's frame: one closed shadow host on the portal page, the
 * full-height sheet on the right edge (it covers the page, it never pushes it)
 * with its top bar, or — folded — a tab on that edge. Whether it is folded is
 * remembered in chrome.storage; it starts folded on a viewport under 1280 px.
 * Every repaint rebuilds the root around the one focused element (dom.ts
 * keepFocus), so a background render never takes focus from the portal page. */

import tokens from '../../frontend/src/styles/tokens.css?inline';
import styles from './panel.css?inline';
import { APP_NAME, APP_SHORT } from '../../frontend/src/lib/brand';
import { themeHost } from './appearance';
import { h, keepFocus } from './dom';
import { lineIcon } from './glyphs';
import { FOLDED_KEY, LEGACY_FOLD_KEY, startsFolded, storedFold } from './panel_logic';

const HOST_ID = '__propscout_sheet__';

let stored: boolean | null = null;
const storedReady: Promise<void> = chrome.storage.local
  .get([FOLDED_KEY, LEGACY_FOLD_KEY])
  .then((r) => {
    stored = storedFold(r);
    if (LEGACY_FOLD_KEY in r) void chrome.storage.local.remove(LEGACY_FOLD_KEY);
  })
  .catch(() => { /* storage unavailable: the viewport decides */ });
/* Decided once per page load, then the operator's. */
let folded: boolean | null = null;

export interface Frame {
  /* The property's page in the app; null hides "V aplikaci". */
  appHref: string | null;
  /* Signed in as; null hides the account menu. */
  email: string | null;
  accountOpen: boolean;
  onAccount: (open: boolean) => void;
  /* A pointer went down outside every menu and its trigger (in the sheet or on the page). */
  onOutside: () => void;
  onSignOut: () => void;
  body: () => Node[];
  /* The folded tab's marks after the PS mark: stage, price move, MF. */
  tab: () => Node[];
}

export interface Sheet {
  shadow: ShadowRoot;
  paint: (frame: Frame) => void;
  folded: () => boolean;
  setFolded: (next: boolean) => void;
  /* Still on the page (a portal that rebuilds <html>'s children takes it). */
  connected: () => boolean;
  remove: () => void;
}

/* The sheet: its host made once per page that shows it, its fold state read
 * before the first paint so it never flashes open and shut. */
export async function mountSheet(onFold: () => void): Promise<Sheet> {
  await storedReady;
  if (folded == null) folded = startsFolded(stored, window.innerWidth);

  document.getElementById(HOST_ID)?.remove();
  const host = h('div', '');
  host.id = HOST_ID;
  themeHost(host);
  document.documentElement.appendChild(host);
  const shadow = host.attachShadow({ mode: 'closed' });
  const style = document.createElement('style');
  style.textContent = tokens + styles;
  const root = h('div', '');
  shadow.append(style, root);

  let last: Frame | null = null;
  const setFolded = (next: boolean): void => {
    folded = next;
    void chrome.storage.local.set({ [FOLDED_KEY]: next }).catch(() => {});
    onFold();
  };

  /* A click outside every menu closes them; one inside the sheet arrives here,
   * one on the page at the document (targeting the host). */
  const outside = (e: Event): void => {
    if (last != null && !e.composedPath().some((n) => n instanceof HTMLElement && n.dataset.menu != null)) {
      last.onOutside();
    }
  };
  const onPage = (e: Event): void => { if (e.target !== host) outside(e); };
  shadow.addEventListener('pointerdown', outside);
  document.addEventListener('pointerdown', onPage, true);

  function topBar(frame: Frame): HTMLElement {
    const bar = h('div', 'top');
    const word = h('span', 'word', APP_NAME.replace(/\.cz$/, ''));
    if (APP_NAME.endsWith('.cz')) word.appendChild(h('span', '', '.cz'));
    bar.appendChild(word);
    if (frame.appHref != null) {
      const link = h('a', 'toplink', 'V aplikaci');
      link.dataset.key = 'app-link';
      link.href = frame.appHref;
      link.target = '_blank';
      link.rel = 'noopener';
      link.title = 'Otevřít v aplikaci';
      link.insertAdjacentHTML('beforeend', lineIcon('out'));
      bar.appendChild(link);
    }
    if (frame.email != null) {
      const more = h('button', 'ib');
      more.innerHTML = lineIcon('more');
      more.dataset.key = 'account';
      more.dataset.menu = 'account';
      more.setAttribute('aria-label', 'Účet');
      more.title = 'Účet';
      more.setAttribute('aria-haspopup', 'menu');
      more.setAttribute('aria-expanded', String(frame.accountOpen));
      more.onclick = () => {
        frame.onAccount(!frame.accountOpen);
        shadow.querySelector<HTMLElement>('[data-key="sign-out"]')?.focus();
      };
      more.onkeydown = (e) => {
        if (e.key !== 'Escape' || !frame.accountOpen) return;
        e.stopPropagation();
        frame.onAccount(false);
      };
      bar.appendChild(more);
      if (frame.accountOpen) {
        const menu = h('div', 'menu');
        menu.dataset.menu = 'account';
        menu.setAttribute('role', 'menu');
        // The menu's name says who is signed in; the visible line repeats it outside the a11y tree.
        menu.setAttribute('aria-label', `Účet ${frame.email}`);
        const who = h('p', 'menu-who meta', frame.email);
        who.title = frame.email;
        who.setAttribute('aria-hidden', 'true');
        const sep = h('div', 'sep');
        sep.setAttribute('role', 'separator');
        const out = h('button', 'mi', 'Odhlásit');
        out.setAttribute('role', 'menuitem');
        out.dataset.key = 'sign-out';
        out.onclick = () => { frame.onAccount(false); frame.onSignOut(); };
        menu.append(who, sep, out);
        menu.onkeydown = (e) => {
          if (e.key !== 'Escape') return;
          e.stopPropagation();  // ours, not the portal's (lightboxes close on Escape too)
          frame.onAccount(false);
          shadow.querySelector<HTMLElement>('[data-key="account"]')?.focus();
        };
        bar.appendChild(menu);
      }
    }
    const fold = h('button', 'ib');
    fold.innerHTML = lineIcon('fold');
    fold.dataset.key = 'fold';
    fold.setAttribute('aria-label', 'Sbalit k okraji');
    fold.title = 'Sbalit k okraji';
    fold.setAttribute('aria-expanded', 'true');
    fold.onclick = () => setFolded(true);
    bar.appendChild(fold);
    return bar;
  }

  function paint(frame: Frame): void {
    last = frame;
    const body = root.querySelector<HTMLElement>('.body');
    const top = body?.scrollTop ?? 0;
    const lists = [...root.querySelectorAll<HTMLElement>('.list')].map((l) => l.scrollTop);
    keepFocus(shadow, () => {
      if (folded) {
        const tab = h('button', 'tab');
        tab.dataset.key = 'fold';
        tab.title = 'Rozbalit panel';
        tab.setAttribute('aria-expanded', 'false');
        // Named by its content (no aria-label), so the marks it shows are announced too.
        const mark = h('span', 'tab-mark', APP_SHORT);
        mark.setAttribute('aria-hidden', 'true');
        tab.append(h('span', 'sr', `${APP_NAME} — rozbalit panel`), mark, ...frame.tab());
        tab.onclick = () => setFolded(false);
        root.replaceChildren(tab);
        return;
      }
      const sheet = h('aside', 'sheet');
      sheet.setAttribute('aria-label', APP_NAME);
      const scroll = h('div', 'body');
      scroll.append(...frame.body());
      sheet.append(topBar(frame), scroll);
      root.replaceChildren(sheet);
    });
    const next = root.querySelector<HTMLElement>('.body');
    if (next != null) next.scrollTop = top;
    root.querySelectorAll<HTMLElement>('.list').forEach((l, i) => { l.scrollTop = lists[i] ?? 0; });
  }

  return {
    shadow,
    paint,
    folded: () => folded === true,
    setFolded,
    connected: () => host.isConnected,
    remove: () => {
      document.removeEventListener('pointerdown', onPage, true);
      host.remove();
    },
  };
}

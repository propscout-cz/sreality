/* The side panel's frame — sidepanel.html, our own page in Chrome's side panel,
 * which shrinks the portal page beside it: the top bar (the wordmark, "V aplikaci",
 * the account menu, the close) over the scrolling body. Every repaint rebuilds the
 * page around the one focused element (dom.ts keepFocus). */

import { APP_NAME } from '../../frontend/src/lib/brand';
import { h, keepFocus } from './dom';
import { lineIcon } from './glyphs';

interface Frame {
  /* The property's page in the app; null hides "V aplikaci". */
  appHref: string | null;
  /* Signed in as; null hides the account menu. */
  email: string | null;
  accountOpen: boolean;
  onAccount: (open: boolean) => void;
  /* A pointer went down outside every menu and its trigger. */
  onOutside: () => void;
  onSignOut: () => void;
  body: () => Node[];
}

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
      document.querySelector<HTMLElement>('[data-key="sign-out"]')?.focus();
    };
    more.onkeydown = (e) => {
      if (e.key === 'Escape' && frame.accountOpen) frame.onAccount(false);
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
        frame.onAccount(false);
        document.querySelector<HTMLElement>('[data-key="account"]')?.focus();
      };
      bar.appendChild(menu);
    }
  }
  // Closing our own page closes Chrome's side panel; the toolbar icon or the edge tab opens it again.
  const close = h('button', 'ib');
  close.innerHTML = lineIcon('close');
  close.dataset.key = 'close';
  close.setAttribute('aria-label', 'Zavřít panel');
  close.title = 'Zavřít panel';
  close.onclick = () => window.close();
  bar.appendChild(close);
  return bar;
}

/* The frame's painter; a pointer going down outside every menu reaches the last frame painted. */
export function mountFrame(): (frame: Frame) => void {
  let last: Frame | null = null;
  document.addEventListener('pointerdown', (e) => {
    if (last != null && !(e.target instanceof Element && e.target.closest('[data-menu]'))) last.onOutside();
  });

  return (frame) => {
    last = frame;
    const top = document.querySelector<HTMLElement>('.body')?.scrollTop ?? 0;
    const lists = [...document.querySelectorAll<HTMLElement>('.list')].map((l) => l.scrollTop);
    keepFocus(document, () => {
      const body = h('main', 'body');
      body.setAttribute('aria-label', APP_NAME);
      body.append(...frame.body());
      document.body.replaceChildren(topBar(frame), body);
    });
    const next = document.querySelector<HTMLElement>('.body');
    if (next != null) next.scrollTop = top;
    document.querySelectorAll<HTMLElement>('.list').forEach((l, i) => { l.scrollTop = lists[i] ?? 0; });
  };
}

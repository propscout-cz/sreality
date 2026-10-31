/* The panel when there is no property to show: no ad in the active tab, signed
 * out, an ad we do not hold (with the add-to-database flow), a failure, and the
 * static skeleton that stands while the first answer is out. One card each, the
 * old panel's words. */

import { buildCard } from './card';
import { h, setBusy } from './dom';
import { INGEST_STEP_CS, type IngestStep } from './ingest_flow';

function primary(text: string, key: string, busy: boolean, onClick: () => void): HTMLElement {
  const btn = h('button', 'cta', text);
  btn.dataset.key = key;
  setBusy(btn, busy);
  btn.onclick = () => { if (!busy) onClick(); };
  return btn;
}

function alertLine(text: string): HTMLElement {
  const p = h('p', 'err', text);
  p.setAttribute('role', 'alert');
  return p;
}

/* The active tab shows no ad: a search page, a page off our portals — or a listing whose
 * page predates an extension update (the panel cannot tell: only a reload reconnects it). */
export function emptyCard(): HTMLElement {
  const card = buildCard({ title: 'Žádný inzerát' });
  const text = 'Vyberte inzerát na podporovaném portálu. Pokud ho máte otevřený, obnovte stránku.';
  card.body.appendChild(h('p', '', text));
  return card.section;
}

/* Every route needs a session: one clean prompt instead of a raw 401. */
export function signedOutCard(busy: boolean, error: string | null, onSignIn: () => void): HTMLElement {
  const card = buildCard({ title: 'Přihlášení' });
  card.body.appendChild(h('p', '', 'Pro zobrazení dat se prosím přihlaste.'));
  if (error != null) card.body.appendChild(alertLine(error));
  card.body.appendChild(primary(busy ? 'Přihlašuji…' : 'Přihlásit se přes Google', 'sign-in', busy, onSignIn));
  return card.section;
}

/* An ad we do not hold: the offer to add it; while the flow runs its step stands
 * where the button was, and a refusal is one sentence above the button. */
export function notFoundCard(
  step: IngestStep | null, message: string | null, forbidden: boolean, onIngest: () => void,
): HTMLElement {
  const card = buildCard({ title: 'Není v databázi' });
  card.body.appendChild(h('p', '', 'Tato nemovitost zatím není v naší databázi.'));
  if (step != null) {
    const p = h('p', 'meta', INGEST_STEP_CS[step]);
    p.setAttribute('role', 'status');
    card.body.appendChild(p);
    return card.section;
  }
  if (message != null) card.body.appendChild(alertLine(message));
  if (!forbidden) card.body.appendChild(primary('Přidat do databáze', 'ingest', false, onIngest));
  return card.section;
}

/* A failed lookup is retried whole; a reload or a missing API config is not fixed by asking again. */
export function errorCard(message: string, retry: (() => void) | null): HTMLElement {
  const card = buildCard({ title: 'Chyba' });
  card.body.appendChild(alertLine(message));
  if (retry != null) card.body.appendChild(primary('Zkusit znovu', 'retry', false, retry));
  return card.section;
}

/* Static (no pulse): the header's lines and the three cards' frames. */
export function skeleton(): Node[] {
  const block = (w: string, ht: number): HTMLElement => {
    const b = h('span', 'sk');
    b.style.width = w;
    b.style.height = `${ht}px`;
    return b;
  };
  const head = h('div', 'sk-head');
  head.setAttribute('aria-hidden', 'true');
  head.append(block('45%', 12), block('70%', 22), block('55%', 14), block('60%', 30));
  const cards = ['Vývoj ceny', 'Moje', 'AI agenti'].map((title, i) => {
    const card = buildCard({ title, variant: i === 2 ? 'agcard' : undefined });
    card.body.append(block('100%', i === 0 ? 96 : 32), block(i === 0 ? '40%' : '70%', 12));
    card.section.setAttribute('aria-hidden', 'true');
    return card.section;
  });
  const status = h('p', 'sr', 'Načítám data…');
  status.setAttribute('role', 'status');
  return [status, head, ...cards];
}

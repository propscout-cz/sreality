/* THE panel's section card (.interface-design/system.md § Section cards, X1): a
 * <section> whose title bar holds the title, an optional bare count and optional
 * actions pushed right, over a body. Every titled block of the panel is built
 * here, so none draws its own frame or small-caps eyebrow. No `chrome` and no
 * global `document` at import, so `card.test.ts` runs under node. */

export interface CardOptions {
  title: string;
  /* A bare number, 0 included (an empty section keeps its count); omit it
   * while the number is unknown (not loaded, or failed). */
  count?: number | null;
  actions?: readonly HTMLElement[];
}

export interface Card {
  section: HTMLElement;
  title: HTMLElement;
  body: HTMLElement;
}

export function buildCard(opts: CardOptions, doc: Document = document): Card {
  const section = doc.createElement('section');
  section.className = 'card';

  const bar = doc.createElement('div');
  bar.className = 'card-bar';
  const title = doc.createElement('h3');
  title.className = 'card-title';
  title.textContent = opts.title;
  bar.appendChild(title);
  if (opts.count != null) {
    const count = doc.createElement('span');
    count.className = 'card-count';
    count.textContent = String(opts.count);
    bar.appendChild(count);
  }
  if (opts.actions != null && opts.actions.length > 0) {
    const actions = doc.createElement('div');
    actions.className = 'card-actions';
    for (const a of opts.actions) actions.appendChild(a);
    bar.appendChild(actions);
  }
  section.appendChild(bar);

  const body = doc.createElement('div');
  body.className = 'card-body';
  section.appendChild(body);
  return { section, title, body };
}

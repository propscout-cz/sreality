/* Cards on a search page that are NOT listings — the portal's paid ads, project
 * promos, "save this search" tiles set into the results grid — washed whitish,
 * so the eye skips them; hovering one lifts the wash.
 *
 * Found by structure, never by a portal's markup, like the cards themselves:
 * each listing card's ITEM is its highest ancestor holding no other listing,
 * and an item's parent holding several of them is a results grid. Any other
 * child of that grid the size of a card — it holds no listing link — is not a
 * listing. Pagination, sort bars and headings are too small to count. A grid
 * of fewer than MIN_CARDS listings is not trusted to be one.
 *
 * Opacity on the element itself, not a layer over it: it needs no positioning
 * of the portal's node and no pseudo-element of its own, and on the portals'
 * white pages it reads as a white wash. It is not the hidden-card veil (that
 * one also greys, and keeps our controls up). */

export const PROMO_ATTR = 'data-mf-promo';
const STYLE_ID = '__mf_promo_style__';
const MIN_CARDS = 3;
/* A grid child this much smaller than the median listing item is chrome, not a card. */
const MIN_AREA_SHARE = 0.3;
const MIN_HEIGHT = 100;

const CSS = `
  [${PROMO_ATTR}] {
    opacity: 0.22 !important;
    transition: opacity 160ms ease !important;
  }
  [${PROMO_ATTR}]:hover { opacity: 0.85 !important; }
`;

export interface PromoWash {
  /* Re-judge the page against every listing card on it now. */
  update: (cards: Iterable<HTMLElement>) => void;
  /* Unwash everything (the overlay stopped). */
  reset: () => void;
}

const area = (e: Element): number => {
  const r = e.getBoundingClientRect();
  return r.width * r.height;
};

export function createPromoWash(): PromoWash {
  let washed = new Set<HTMLElement>();

  function update(cardList: Iterable<HTMLElement>): void {
    const cards = new Set(cardList);
    // How many listing cards each ancestor holds.
    const held = new Map<Element, number>();
    for (const card of cards) {
      for (let e: Element | null = card; e != null && e !== document.body; e = e.parentElement) {
        held.set(e, (held.get(e) ?? 0) + 1);
      }
    }
    const grids = new Map<Element, Element[]>();
    for (const card of cards) {
      if (!card.isConnected) continue;
      let item: Element = card;
      while (item.parentElement != null && held.get(item.parentElement) === 1) {
        item = item.parentElement;
      }
      const grid = item.parentElement;
      if (grid == null || grid === document.body || grid === document.documentElement) continue;
      const items = grids.get(grid) ?? [];
      if (!items.includes(item)) items.push(item);
      grids.set(grid, items);
    }

    const next = new Set<HTMLElement>();
    for (const [grid, items] of grids) {
      if (items.length < MIN_CARDS) continue;
      const areas = items.map(area).filter((a) => a > 0).sort((a, b) => a - b);
      if (areas.length === 0) continue;
      const median = areas[Math.floor(areas.length / 2)];
      for (const child of Array.from(grid.children)) {
        if (!(child instanceof HTMLElement) || held.has(child)) continue;
        const r = child.getBoundingClientRect();
        if (r.height < MIN_HEIGHT || r.width * r.height < MIN_AREA_SHARE * median) continue;
        next.add(child);
      }
    }

    for (const e of washed) if (!next.has(e)) e.removeAttribute(PROMO_ATTR);
    for (const e of next) if (!washed.has(e)) e.setAttribute(PROMO_ATTR, '');
    washed = next;
    if (next.size > 0 && document.getElementById(STYLE_ID) == null) {
      const style = document.createElement('style');
      style.id = STYLE_ID;
      style.textContent = CSS;
      (document.head ?? document.documentElement).appendChild(style);
    }
  }

  function reset(): void {
    for (const e of washed) e.removeAttribute(PROMO_ATTR);
    washed = new Set();
  }

  return { update, reset };
}

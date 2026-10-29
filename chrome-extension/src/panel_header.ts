/* The side sheet's header (on the panel's own background, not a card) and the
 * folded tab's marks. Grain, field by field (MS11): the price, its per-m², the
 * live colour and the move pill are THIS ad's; what the property is (status, days
 * on market, facts, MF) is the property's. Every label and number comes from the
 * app's own modules. */

import { categoryMainLabel, categoryTypeLabel, enumLabel, subtypeLabel } from '../../frontend/src/lib/enums';
import {
  fmtArea, fmtCzk, fmtFloorTag, fmtMeasuredPricePerM2, fmtPct, fmtTomDays,
} from '../../frontend/src/lib/format';
import { areaBasisLabel, areaKindOf, ppm2BasisFromToken } from '../../frontend/src/lib/measure';
import { mfShape, mfYieldText } from '../../frontend/src/lib/mfReference';
import type { PipelineStage } from '../../frontend/src/lib/types';
import { h } from './dom';
import { codeBadge, lineIcon, stageLook } from './glyphs';
import { factChips, tabMarkList } from './panel_logic';
import type { PortalListing } from './types';

const MF_TITLE = 'Hrubý výnos dle cenové mapy nájemného MF (nájem ÷ cena)';

/* This ad's last price move, by magnitude with its arrow (a cut is good news here: sage). */
export function moveBadge(pct: number): HTMLElement {
  const down = pct < 0;
  const pill = h('span', `move ${down ? 'move--down' : 'move--up'}`, `${down ? '↓' : '↑'} ${fmtPct(Math.abs(pct))}`);
  pill.title = `Poslední změna ceny inzerátu: ${fmtPct(pct, { signed: true })}`;
  return pill;
}

function mfTitle(l: PortalListing): string {
  const shape = mfShape(l.mf_reference_rent);
  return shape.kind === 'range' && shape.note != null ? `${MF_TITLE} — ${shape.note}` : MF_TITLE;
}

/* "Byt 3+kk, 78 m²": the type, the disposition when there is one, the headline
 * area (a plot's says so). */
export function titleText(l: PortalListing): string {
  const kind = [categoryMainLabel(l.category_main), l.disposition].filter(Boolean).join(' ');
  return l.area_m2 == null ? kind : `${kind}, ${fmtArea(l.area_m2, areaKindOf(l.category_main))}`;
}

export function header(l: PortalListing, move: number | null): HTMLElement {
  const head = h('header', 'head');
  const ident = h('div', 'ident');

  const eyebrow = h('p', 'eyebrow meta');
  const parts: Node[][] = [];
  if (l.category_type != null) parts.push([document.createTextNode(categoryTypeLabel(l.category_type))]);
  const active = l.property_is_active ?? l.is_active;
  if (active != null) {
    const dot = h('span', active ? 'dot dot--live' : 'dot');
    dot.setAttribute('aria-hidden', 'true');
    parts.push([dot, document.createTextNode(active ? 'Aktivní' : 'Neaktivní')]);
  }
  if (l.tom_days != null) parts.push([h('span', 'num', `${fmtTomDays(l.tom_days)} na trhu`)]);
  parts.forEach((nodes, i) => {
    if (i > 0) eyebrow.appendChild(h('span', 'sepdot', '·'));
    eyebrow.append(...nodes);
  });
  if (parts.length > 0) ident.appendChild(eyebrow);

  const title = h('h1', 'title', titleText(l));
  const basis = areaBasisLabel(l.area_basis);
  if (basis != null) title.title = `Plocha ${basis}`;
  ident.appendChild(title);

  if (l.display_label) {
    const addr = h('p', 'addr');
    addr.innerHTML = lineIcon('pin');
    addr.appendChild(document.createTextNode(l.display_label));
    ident.appendChild(addr);
  }

  const facts = factChips(l, {
    floor: fmtFloorTag,
    label: (column, slug) => (column === 'subtype' ? subtypeLabel(slug) : enumLabel(column, slug)),
    plot: (m2) => fmtArea(m2, 'plot'),
  });
  if (facts.length > 0) {
    const row = h('div', 'facts');
    for (const f of facts) {
      const chip = h('span', 'fact', f.text);
      if (f.title != null && f.title !== f.text) chip.title = f.title;
      row.appendChild(chip);
    }
    ident.appendChild(row);
  }
  head.appendChild(ident);

  const pricing = h('div', 'pricing');
  const col = h('div', 'price-col');
  col.appendChild(l.price_czk == null
    ? h('p', 'price price--none', 'Cena na vyžádání')
    : h('p', l.is_active ? 'price price--live' : 'price', fmtCzk(l.price_czk)));
  const sub = h('p', 'price-sub');
  if (move != null && move !== 0) sub.appendChild(moveBadge(move));
  const ppm = fmtMeasuredPricePerM2(l.price_per_m2, ppm2BasisFromToken(l.price_per_m2_basis));
  if (ppm !== '—') sub.appendChild(h('span', 'mono meta', ppm));
  if (sub.childElementCount > 0) col.appendChild(sub);
  pricing.appendChild(col);

  const mf = mfYieldText(l.mf_gross_yield_pct, l.mf_reference_rent);
  if (mf != null) {
    const box = h('div', 'mf');
    box.title = mfTitle(l);
    box.append(h('p', 'l4', 'Výnos MF'), h('p', 'mono mf-val', mf), h('p', 'meta', 'hrubý'));
    pricing.appendChild(box);
  }
  head.appendChild(pricing);
  return head;
}

/* The folded tab, after the PS mark: the deal's stage badge, this ad's price drop, MF. */
export function tabMarks(
  l: PortalListing | null, move: number | null, stages: readonly PipelineStage[] | null,
): Node[] {
  if (l == null || !l.found) return [];
  const look = stageLook(l.pipeline);
  const code = look && codeBadge(look, stages);
  if (code != null) code.title = `V pipeline: ${l.pipeline?.stage_label ?? ''}`;
  const mf = mfYieldText(l.mf_gross_yield_pct, l.mf_reference_rent);
  let box: HTMLElement | null = null;
  if (mf != null) {
    box = h('span', 'tab-mf');
    box.title = MF_TITLE;
    box.append(h('span', 'l4', 'MF'), h('span', 'mono', mf));
  }
  return tabMarkList<Node>(code, move, box, moveBadge);
}

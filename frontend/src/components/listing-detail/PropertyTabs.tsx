/* The property page's tabs (operator ruling 2026-10-22, design A; 2026-10-23: the
 * registered sales joined Cena a hodnota, the notes moved under the header): one
 * tab per question the page answers, so nothing sits nine screens down. The open tab
 * lives in the address (`?tab=`), switched with `replace` so Back still leaves
 * the page; an address with no `?tab=` opens the tab its other parts point at
 * (an estimation run, an advert row), else the overview. A tab is mounted the
 * first time it opens and then kept, hidden: a half-typed note or a picked
 * radius survives a look at another tab. */
import { useState, type ReactNode } from 'react';
import { useLocation, useSearchParams } from 'react-router-dom';

import Tabs from '@/components/Tabs';

export type PropertyTabId = 'prehled' | 'fotky' | 'hodnota' | 'okoli' | 'inzeraty' | 'audit';

export const PROPERTY_TABS: ReadonlyArray<{ id: PropertyTabId; label: string }> = [
  { id: 'prehled', label: 'Přehled' },
  { id: 'fotky', label: 'Fotky' },
  { id: 'hodnota', label: 'Cena a hodnota' },
  { id: 'okoli', label: 'Okolí dashboard' },
  { id: 'inzeraty', label: 'Inzeráty' },
  { id: 'audit', label: 'Audit' },
];

/* Tabs that were folded into others, kept for the addresses already handed out. */
const RETIRED: Record<string, PropertyTabId> = { trh: 'hodnota', moje: 'prehled' };

const isTabId = (v: string | null): v is PropertyTabId => PROPERTY_TABS.some((t) => t.id === v);

/* `?tab=` wins. Otherwise the links handed out before the tabs keep landing on
 * their section: `?run=` / `#estimations` / `#feedback` (the estimations list, the
 * old /estimation/:id) on Cena a hodnota, `?letters=` and an `?advert=` that is
 * not the canonical one (the review pages, every old advert address) on Inzeráty.
 * The canonical advert's own address is the header, so it opens the overview. */
export function tabFromAddress(
  params: URLSearchParams,
  hash: string,
  canonicalListingId: number | null,
): PropertyTabId {
  const asked = params.get('tab');
  if (isTabId(asked)) return asked;
  if (asked != null && asked in RETIRED) return RETIRED[asked];
  if (params.has('run') || hash === '#estimations' || hash === '#feedback') return 'hodnota';
  const advert = Number(params.get('advert')) || null;
  if (params.has('letters') || (advert != null && advert !== canonicalListingId)) return 'inzeraty';
  return 'prehled';
}

export function usePropertyTab(canonicalListingId: number | null) {
  const [params, setParams] = useSearchParams();
  const { hash } = useLocation();
  const tab = tabFromAddress(params, hash, canonicalListingId);
  const setTab = (id: PropertyTabId) =>
    setParams(
      (prev) => {
        const next = new URLSearchParams(prev);
        next.set('tab', id);
        return next;
      },
      { replace: true },
    );
  return [tab, setTab] as const;
}

export function PropertyTabs({
  tab,
  onSelect,
  counts = {},
  panels,
}: {
  tab: PropertyTabId;
  onSelect: (id: PropertyTabId) => void;
  counts?: Partial<Record<PropertyTabId, number>>;
  panels: Record<PropertyTabId, ReactNode>;
}) {
  const [visited, setVisited] = useState<ReadonlySet<PropertyTabId>>(() => new Set([tab]));
  if (!visited.has(tab)) setVisited(new Set(visited).add(tab));

  /* The shared strip (components/Tabs.tsx) draws the bar, its keys and the
   * `ptab-*` / `ppanel-*` ids; a count shows only above zero. It stays a direct
   * child of this wrapper, so it sticks for as long as the panels scroll. */
  return (
    <div className="mt-6">
      <Tabs
        tabs={PROPERTY_TABS.map(({ id, label }) => ({
          key: id,
          label,
          count: (counts[id] ?? 0) > 0 ? counts[id] : undefined,
        }))}
        active={tab}
        onChange={onSelect}
        ariaLabel="Části stránky"
        idPrefix="p"
        sticky
      />
      {PROPERTY_TABS.map(({ id }) =>
        visited.has(id) || id === tab ? (
          <div
            key={id}
            id={`ppanel-${id}`}
            role="tabpanel"
            aria-labelledby={`ptab-${id}`}
            hidden={id !== tab}
            className="pt-6"
          >
            {panels[id]}
          </div>
        ) : null,
      )}
    </div>
  );
}

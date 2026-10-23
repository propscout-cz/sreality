/* The property page's tabs (operator ruling 2026-10-22, design A; 2026-10-23: the
 * registered sales joined Cena a hodnota, the notes moved under the header): one
 * tab per question the page answers, so nothing sits nine screens down. The open tab
 * lives in the address (`?tab=`), switched with `replace` so Back still leaves
 * the page; an address with no `?tab=` opens the tab its other parts point at
 * (an estimation run, an advert row), else the overview. A tab is mounted the
 * first time it opens and then kept, hidden: a half-typed note or a picked
 * radius survives a look at another tab. */
import { useState, type KeyboardEvent, type ReactNode } from 'react';
import { useLocation, useSearchParams } from 'react-router-dom';

export type PropertyTabId = 'prehled' | 'fotky' | 'hodnota' | 'inzeraty' | 'audit';

export const PROPERTY_TABS: ReadonlyArray<{ id: PropertyTabId; label: string }> = [
  { id: 'prehled', label: 'Přehled' },
  { id: 'fotky', label: 'Fotky' },
  { id: 'hodnota', label: 'Cena a hodnota' },
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

  /* Arrow keys move along the bar (the WAI-ARIA tabs pattern); Tab leaves it. */
  const onKeyDown = (e: KeyboardEvent<HTMLDivElement>) => {
    const step = e.key === 'ArrowRight' ? 1 : e.key === 'ArrowLeft' ? -1 : 0;
    if (step === 0 && e.key !== 'Home' && e.key !== 'End') return;
    e.preventDefault();
    const i = PROPERTY_TABS.findIndex((t) => t.id === tab);
    const n = PROPERTY_TABS.length;
    const to = e.key === 'Home' ? 0 : e.key === 'End' ? n - 1 : (i + step + n) % n;
    onSelect(PROPERTY_TABS[to].id);
    document.getElementById(`ptab-${PROPERTY_TABS[to].id}`)?.focus();
  };

  return (
    <div className="mt-6">
      <div
        role="tablist"
        aria-label="Části stránky"
        onKeyDown={onKeyDown}
        className="sticky top-0 z-10 -mx-2 flex gap-1 overflow-x-auto border-b border-[var(--color-rule-strong)] bg-[var(--color-paper)] px-2"
      >
        {PROPERTY_TABS.map(({ id, label }) => {
          const selected = id === tab;
          const count = counts[id];
          return (
            <button
              key={id}
              id={`ptab-${id}`}
              type="button"
              role="tab"
              aria-selected={selected}
              aria-controls={`ppanel-${id}`}
              tabIndex={selected ? 0 : -1}
              onClick={() => onSelect(id)}
              className={[
                'inline-flex h-11 shrink-0 items-center gap-2 whitespace-nowrap border-b-2 px-3 text-sm transition-colors',
                selected
                  ? 'border-[var(--color-copper)] font-medium text-[var(--color-ink)]'
                  : 'border-transparent text-[var(--color-ink-3)] hover:text-[var(--color-ink)]',
              ].join(' ')}
            >
              {label}
              {count != null && count > 0 && (
                <span className="rounded-full bg-[var(--color-inset)] px-1.5 py-0.5 font-mono text-[0.68rem] tabular-nums text-[var(--color-ink-2)]">
                  {count}
                </span>
              )}
            </button>
          );
        })}
      </div>
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

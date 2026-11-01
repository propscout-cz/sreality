/* Audit poloh — the listings customers cannot see yet (migration 524).
 *
 * W5's consumer rule: a listing reaches Browse, the map, the feed, the watchdog
 * and dedup only once the location store has an ANSWER for it. This page is that
 * exempted set, and it is a WORK QUEUE — a row leaves it the moment the resolver
 * lane places the listing.
 *
 * W15 (operator ruling, 2026-09-14) makes that EVERY listing in the database, not
 * only the ones a consumer surface would reach: the served set retired, and a
 * delisted listing with no decided location is a finding like any other. Live vs
 * delisted is a `quality` filter below, never a reason to hide a row from here.
 *
 * No map. The whole subject of the page is rows with no point to draw; the old
 * map drew their LEGACY coordinates, and migration 508 deleted those columns.
 *
 * TWO STATES (W7-b, migration 518), and the page always looks at exactly one:
 * "čeká na zpracování" is the lane still working (no verdict yet, queued for
 * one, or the ad changed after the verdict) — normal traffic; "zpracováno,
 * nerozhodnuto" is a finished verdict with no location — the issue.
 *
 * W1 (ruling Q7, nothing measured twice) deleted every count this page held of
 * its own: the waterfall, the summary RPC's matrix, the two state totals and
 * the split, and the nav badge. The location gauge's stored cells (Health, the
 * Location Quality page) are the one location count; the one number here is
 * the exact size of the filtered list, read with its first page.
 */

import { useState } from 'react';

import { SegmentedBar } from '@/components/controls';
import ErrorBanner from '@/components/ErrorBanner';
import InfiniteSentinel from '@/components/InfiniteSentinel';
import Panel from '@/components/Panel';
import Spinner from '@/components/Spinner';

import { CATEGORY_MAIN_LABELS, categoryMainLabelPlural, categoryMainLabel, categoryTypeLabel } from '@/lib/enums';
import { fmtArea, fmtCount, fmtCzk, fmtDateSlash } from '@/lib/format';
import { LOCATION_SOURCES } from '@/lib/locationQuality';
import { stepLabel } from '@/lib/locationSteps';
import { propertyPath } from '@/lib/listingUrl';
import { portalLabel } from '@/lib/portals';
import { useInfiniteList } from '@/lib/useInfiniteList';
import type { SortSpec } from '@/lib/queries';
import type { KeysetCursor } from '@/lib/keyset';
import {
  EMPTY_PIN_AUDIT_FILTERS,
  PIN_AUDIT_PAGE_SIZE,
  PIN_AUDIT_QUALITIES,
  PIN_AUDIT_STATES,
  fetchPinAuditPage,
  type PinAuditFilters,
  type PinAuditPage,
  type PinAuditQuality,
  type PinAuditRow,
  type PinAuditState,
} from '@/lib/pinAudit';
import { Link } from 'react-router-dom';

/* ------------------------------------------------------------------ chrome */

const HEAD = 'text-left text-[0.65rem] tracking-[0.1em] uppercase text-[var(--color-ink-3)]';
const TH = 'py-1.5 pr-3 font-medium whitespace-nowrap';
const TD = 'py-1.5 pr-3 align-top';
const NUM = 'py-1.5 pr-3 text-right font-mono tabular-nums';
const ROW = 'border-t border-[var(--color-rule-soft)]';

/* The pick-several chip (portal, type, why); every pick-one row is a SegmentedBar. */
function Chip({
  on,
  onClick,
  children,
}: {
  on: boolean;
  onClick: () => void;
  children: React.ReactNode;
}) {
  return (
    <button
      type="button"
      aria-pressed={on}
      onClick={onClick}
      className={[
        'rounded-[var(--radius-sm)] border px-2.5 py-1 text-[0.78rem] transition-colors',
        on
          ? 'border-[var(--color-copper)] bg-[var(--color-copper-soft)] text-[var(--color-ink)]'
          : 'border-[var(--color-rule)] text-[var(--color-ink-2)] hover:border-[var(--color-rule-strong)]',
      ].join(' ')}
    >
      {children}
    </button>
  );
}

/* -------------------------------------------------------------- vocabulary */

/* Plain Czech, and deliberately not a shorter lie. "Bez dat o poloze" would
 * have been wrong: those ads DO have raw evidence in the claim store — what
 * they have no trace of is evidence the saved verdict actually used. */
const QUALITY_LABEL: Record<PinAuditQuality, string> = {
  active_no_claims: 'inzerát běží · nebylo z čeho polohu určit',
  active_unresolved: 'inzerát běží · podklady byly, poloha nevyšla',
  delisted_no_claims: 'inzerát stažen · nebylo z čeho polohu určit',
  delisted_unresolved: 'inzerát stažen · podklady byly, poloha nevyšla',
};

/* The two states, in the operator's words and with the verdict attached: one of
 * them is a finding and the other is not, and the label has to say so.
 *
 * ONE NAME PER STATE (W16): the name comes from the shared wording module, and
 * nothing here re-types it. */
const STATE_LABEL: Record<PinAuditState, string> = {
  pending: stepLabel('hidden_pending', 'cs'),
  unresolved: stepLabel('hidden_unresolved', 'cs'),
};

const STATE_NOTE: Record<PinAuditState, string> = {
  pending: 'není problém, systém je zatím nestihl zpracovat',
  unresolved: 'toto je problém',
};

const STATUS_OPTIONS: ReadonlyArray<{ value: PinAuditFilters['status']; label: string }> = [
  { value: 'all', label: 'Vše' },
  { value: 'active', label: 'Běží' },
  { value: 'delisted', label: 'Stažené' },
];

const SIBLING_OPTIONS: ReadonlyArray<{ value: PinAuditFilters['sibling']; label: string }> = [
  { value: 'all', label: 'Vše' },
  { value: 'yes', label: 'Má' },
  { value: 'no', label: 'Nemá' },
];

const SORT_OPTIONS = [
  { field: 'last_seen_at' as const, label: 'naposledy viděno' },
  { field: 'first_seen_at' as const, label: 'poprvé viděno' },
];

const QUALITY_SHORT: Record<PinAuditQuality, string> = {
  active_no_claims: 'běží · nebylo z čeho',
  active_unresolved: 'běží · nevyšlo',
  delisted_no_claims: 'stažen · nebylo z čeho',
  delisted_unresolved: 'stažen · nevyšlo',
};

const dash = (v: string | null | undefined): string =>
  v == null || v === '' ? '—' : v;

/* ------------------------------------------------------------------- page */

export default function LocationPinAudit() {
  const [filters, setFilters] = useState<PinAuditFilters>(EMPTY_PIN_AUDIT_FILTERS);
  const [sort, setSort] = useState<SortSpec>({
    field: 'last_seen_at',
    direction: 'desc',
  });

  const filterKey = JSON.stringify(filters);

  const list = useInfiniteList<PinAuditRow, PinAuditPage>({
    queryKey: ['pin-audit', 'list', filterKey, sort.field, sort.direction],
    queryFn: (cursor, signal) =>
      fetchPinAuditPage(filters, sort, cursor as KeysetCursor | null, { signal }),
    pageSize: PIN_AUDIT_PAGE_SIZE,
    getRowId: (r) => r.listing_id,
  });
  const total = list.firstPage?.total ?? null;
  /* One refresh across the relation (the hourly matview refresh): the list's own stamp. */
  const refreshedAt = list.rows[0]?.refreshed_at ?? null;

  const toggle = <T extends string>(list: ReadonlyArray<T>, v: T): T[] =>
    list.includes(v) ? list.filter((x) => x !== v) : [...list, v];

  const err = list.error;

  return (
    <div className="px-6 pt-5 pb-10 max-w-screen-xl mx-auto">
      <h1 className="text-2xl leading-tight">Audit poloh</h1>
      <p className="mt-2 text-[0.85rem] leading-relaxed text-[var(--color-ink-2)] max-w-[52rem]">
        Inzeráty, které se zákazníkům nezobrazují, dokud nemají rozhodnutou
        polohu. Nový systém pro určování polohy pro ně zatím žádnou polohu nemá —
        buď neměl z čeho vyjít, nebo podklady měl a polohu z nich nedokázal
        určit — a dokud ji nemá, nejdou vidět nikde: ve vyhledávání, na mapě, v
        hlídacích psech ani při hledání duplicit. Jakmile je systém určí, inzerát
        se objeví sám a z tohoto seznamu zmizí. Je tu každý takový inzerát —
        běžící i stažený; jestli inzerát běží, je jen filtr níže. Jsou tu dvě různé skupiny a
        stránka ukazuje vždy jen jednu z nich: ty, které systém teprve čekají, a
        ty, u kterých už doběhl a polohu neurčil.
      </p>
      {err ? <ErrorBanner message={err.message} /> : null}

      <div className="mt-6 grid gap-5">
        <div className="flex flex-wrap items-center gap-2" data-testid="pin-audit-state-toggle">
          <SegmentedBar
            hug
            label="Skupina inzerátů"
            options={PIN_AUDIT_STATES.map((st) => ({
              value: st,
              label: STATE_LABEL[st],
            }))}
            value={filters.state}
            /* Switching state drops the quality chips with it: they explain a
             * verdict, and under „čeká na zpracování“ there is none. */
            onChange={(st) =>
              setFilters((f) => (f.state === st ? f : { ...f, state: st, qualities: [] }))
            }
          />
          <span className="text-[0.78rem] text-[var(--color-ink-3)]">
            {STATE_NOTE[filters.state]}
          </span>
        </div>

        <Panel
          title="Filtry"
          lede="Filtry platí pro seznam a vždy jen v rámci skupiny vybrané nahoře. Nevybrat nic znamená „bez omezení“."
        >
          <div className="grid gap-3">
            <div>
              <p className="text-[0.7rem] uppercase tracking-[0.1em] text-[var(--color-ink-3)]">
                Portál
              </p>
              <div className="mt-1.5 flex flex-wrap gap-1.5">
                {LOCATION_SOURCES.map((s) => (
                  <Chip
                    key={s}
                    on={filters.sources.includes(s)}
                    onClick={() =>
                      setFilters((f) => ({ ...f, sources: toggle(f.sources, s) }))
                    }
                  >
                    {portalLabel(s) ?? s}
                  </Chip>
                ))}
              </div>
            </div>
            <div>
              <p className="text-[0.7rem] uppercase tracking-[0.1em] text-[var(--color-ink-3)]">
                Druh nemovitosti
              </p>
              <div className="mt-1.5 flex flex-wrap gap-1.5">
                {Object.keys(CATEGORY_MAIN_LABELS).map((c) => (
                  <Chip
                    key={c}
                    on={filters.categories.includes(c)}
                    onClick={() =>
                      setFilters((f) => ({
                        ...f,
                        categories: toggle(f.categories, c),
                      }))
                    }
                  >
                    {categoryMainLabelPlural(c)}
                  </Chip>
                ))}
              </div>
            </div>
            {/* Only under „zpracováno, nerozhodnuto“: these four buckets explain
                a VERDICT, and a listing the system has not finished with has
                none to explain. */}
            <div hidden={filters.state !== 'unresolved'}>
              <p className="text-[0.7rem] uppercase tracking-[0.1em] text-[var(--color-ink-3)]">
                Proč poloha chybí
              </p>
              <div className="mt-1.5 flex flex-wrap gap-1.5">
                {PIN_AUDIT_QUALITIES.map((q) => (
                  <Chip
                    key={q}
                    on={filters.qualities.includes(q)}
                    onClick={() =>
                      setFilters((f) => ({
                        ...f,
                        qualities: toggle(f.qualities, q),
                      }))
                    }
                  >
                    {QUALITY_LABEL[q]}
                  </Chip>
                ))}
              </div>
            </div>
            <div>
              <p className="text-[0.7rem] uppercase tracking-[0.1em] text-[var(--color-ink-3)]">
                Stav inzerátu
              </p>
              <div className="mt-1.5">
                <SegmentedBar
                  hug
                  label="Stav inzerátu"
                  options={STATUS_OPTIONS}
                  value={filters.status}
                  onChange={(status) => setFilters((f) => ({ ...f, status }))}
                />
              </div>
            </div>
            <div>
              <p className="text-[0.7rem] uppercase tracking-[0.1em] text-[var(--color-ink-3)]">
                Jiný inzerát téže nemovitosti má polohu
              </p>
              <div className="mt-1.5">
                <SegmentedBar
                  hug
                  label="Jiný inzerát téže nemovitosti má polohu"
                  options={SIBLING_OPTIONS}
                  value={filters.sibling}
                  onChange={(sibling) => setFilters((f) => ({ ...f, sibling }))}
                />
              </div>
            </div>
            <div>
              <button
                type="button"
                className="text-[0.78rem] text-[var(--color-ink-3)] underline decoration-dotted underline-offset-2 hover:text-[var(--color-ink)]"
                onClick={() =>
                  setFilters((f) => ({ ...EMPTY_PIN_AUDIT_FILTERS, state: f.state }))
                }
              >
                Zrušit všechny filtry
              </button>
            </div>
          </div>
        </Panel>

        <Panel
          title="Seznam"
          lede={`Jeden řádek = jeden inzerát. Název vede na jeho stránku u nás, odkaz „portál“ na původní inzerát. „Verdikt“ shrnuje, co o poloze ví nový systém.${
            refreshedAt ? ` Stav k ${fmtDateSlash(refreshedAt)}; přepočítává se každou hodinu.` : ''
          }`}
        >
          <div className="mb-2 flex flex-wrap items-center gap-3 text-[0.78rem] text-[var(--color-ink-3)]">
            {total != null ? (
              <span className="font-medium tabular-nums text-[var(--color-ink)]" data-testid="pin-audit-total">
                {fmtCount(total)} inzerátů
              </span>
            ) : null}
            <span>Řadit podle:</span>
            {/* A click on the lit order flips its direction; another order starts newest first. */}
            <SegmentedBar
              hug
              label="Řadit podle"
              options={SORT_OPTIONS.map((o) => ({
                value: o.field,
                label:
                  sort.field === o.field ? `${o.label} ${sort.direction === 'desc' ? '↓' : '↑'}` : o.label,
              }))}
              value={sort.field}
              onChange={(field) =>
                setSort((s) =>
                  s.field === field
                    ? { ...s, direction: s.direction === 'desc' ? 'asc' : 'desc' }
                    : { field, direction: 'desc' },
                )
              }
            />
          </div>

          <div className="overflow-x-auto">
            <table className="text-[0.8rem] min-w-full">
              <thead className={HEAD}>
                <tr>
                  <th className={TH}>Portál</th>
                  <th className={TH}>Druh</th>
                  <th className={TH}>Inzerát</th>
                  <th className={`${TH} text-right`}>Cena</th>
                  <th className={TH}>Stav</th>
                  <th className={`${TH} text-right`}>Naposledy viděno</th>
                  <th className={TH}>Proč poloha chybí</th>
                  <th className={TH}>Sourozenec</th>
                  <th className={TH}>Verdikt</th>
                </tr>
              </thead>
              <tbody>
                {list.rows.map((r) => {
                  const href = propertyPath(r.property_id, r.listing_id);
                  const name = [
                    categoryMainLabel(r.category_main),
                    r.disposition,
                    r.area_m2 != null ? fmtArea(r.area_m2) : null,
                  ]
                    .filter(Boolean)
                    .join(' · ');
                  return (
                    <tr
                      key={r.listing_id}
                      className={ROW}
                      data-testid={`pin-audit-row-${r.listing_id}`}
                    >
                      <td className={TD}>{portalLabel(r.source) ?? r.source}</td>
                      <td className={TD}>
                        {categoryTypeLabel(r.category_type)}
                      </td>
                      <td className={TD}>
                        <Link
                          to={href}
                          className="text-[var(--color-copper)] hover:underline"
                        >
                          {name || 'Nemovitost'}
                        </Link>
                        <span className="block text-[var(--color-ink-3)]">
                          {dash(r.display_label)}
                        </span>
                        {r.source_url ? (
                          <a
                            href={r.source_url}
                            target="_blank"
                            rel="noreferrer"
                            className="text-[0.72rem] text-[var(--color-ink-3)] underline decoration-dotted underline-offset-2"
                          >
                            portál ↗
                          </a>
                        ) : null}
                      </td>
                      <td className={NUM}>{fmtCzk(r.price_czk)}</td>
                      <td className={TD}>{r.is_active ? 'běží' : 'stažen'}</td>
                      <td className={NUM}>{fmtDateSlash(r.last_seen_at)}</td>
                      <td className={TD}>
                        {r.state === 'unresolved'
                          ? QUALITY_SHORT[r.quality]
                          : r.has_row
                            ? 'čeká na přepočet'
                            : 'zatím nezpracováno'}
                      </td>
                      <td className={TD}>
                        {r.sibling_has_pin ? (
                          <span className="text-[var(--color-copper)]">
                            má polohu
                          </span>
                        ) : (
                          <span className="text-[var(--color-ink-4)]">—</span>
                        )}
                      </td>
                      <td className={`${TD} text-[var(--color-ink-3)]`}>
                        {r.has_row
                          ? `${dash(r.country_status)} · ${dash(r.granularity)} · ${dash(r.match_confidence)}`
                          : 'nový systém tento inzerát zatím nezpracoval'}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>

          {list.isLoading ? <Spinner /> : null}
          <InfiniteSentinel
            onReach={list.fetchNextPage}
            hasNextPage={list.hasNextPage}
            isFetchingNextPage={list.isFetchingNextPage}
            loadedCount={list.loadedCount}
            total={total}
            loadingLabel="Načítám další…"
          />
        </Panel>
      </div>
    </div>
  );
}

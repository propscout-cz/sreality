/* The location position audit on Location quality (W6-c, migration 733): where every ad's point came from.
 *
 * The grid and the strip draw the location gauge's STORED position facts (one number per kind and grain, ruling Q7);
 * every non-zero number is a button that opens the list of those ads, the cell living in the URL. The list reads the
 * admin-gated `location_position_audit`. Ledger figures (mono tabular), borders only; copper marks the chosen cell and
 * nothing else. Ruling Q3 reads off the grain heads: each carries the map's own precision words. */

import { useState } from 'react';
import { Link } from 'react-router-dom';
import { SegmentedBar } from '@/components/controls';
import ErrorBanner from '@/components/ErrorBanner';
import { fmtNumber } from '@/components/health/CheckRow';
import InfiniteSentinel from '@/components/InfiniteSentinel';
import Panel from '@/components/Panel';
import Spinner from '@/components/Spinner';
import { categoryMainLabel } from '@/lib/enums';
import { fmtArea, fmtDateSlash } from '@/lib/format';
import type { KeysetCursor } from '@/lib/keyset';
import { propertyPath } from '@/lib/listingUrl';
import { portalShort } from '@/lib/portals';
import {
  ALL_KINDS,
  KIND_LABEL,
  POSITION_AUDIT_PAGE_SIZE,
  POSITION_GRAINS,
  UNCLASSIFIED,
  fetchPositionAuditPage,
  grainTotal,
  gridGrains,
  gridKinds,
  kindTotal,
  listFilters,
  precisionWords,
  type PositionAuditPage,
  type PositionAuditRow,
  type PositionCell,
  type PositionGrid,
  type PositionListFilters,
} from '@/lib/positionAudit';
import type { SortSpec } from '@/lib/queries';
import { useInfiniteList } from '@/lib/useInfiniteList';

const HEAD = 'text-[0.65rem] font-medium uppercase tracking-[0.1em] text-[var(--color-ink-3)]';
const ROW = 'border-t border-[var(--color-rule-soft)]';

const kindLabel = (kind: string): string => KIND_LABEL[kind]?.label ?? kind;
const grainLabel = (grain: string | null): string =>
  grain == null ? 'every grain' : POSITION_GRAINS.find((g) => g.key === grain)?.label ?? grain;

const sameCell = (a: PositionCell | null, b: PositionCell): boolean =>
  a != null && a.kind === b.kind && a.grain === b.grain;

/* One figure. A non-zero count the list can show is a button naming its cell; a zero, or a kind the list cannot show
 * (`unclassified`), stays a figure. */
function Count({
  n,
  name,
  cell,
  chosen,
  onOpen,
}: {
  n: number;
  name: string;
  cell: PositionCell | null;
  chosen: PositionCell | null;
  onOpen: (cell: PositionCell) => void;
}) {
  const figure = 'font-mono tabular-nums text-[0.78rem]';
  if (n === 0 || cell == null) {
    return <span className={`${figure} ${n === 0 ? 'text-[var(--color-ink-3)]' : 'text-[var(--color-ink)]'}`}>{fmtNumber(n)}</span>;
  }
  const on = sameCell(chosen, cell);
  return (
    <button
      type="button"
      aria-pressed={on}
      aria-label={`${name}: ${fmtNumber(n)}`}
      onClick={() => onOpen(cell)}
      className={[
        figure,
        'rounded-[var(--radius-xs)] px-1 -mx-1 underline decoration-dotted underline-offset-2',
        on
          ? 'bg-[var(--color-copper-soft)] text-[var(--color-copper)] decoration-[var(--color-copper)]'
          : 'text-[var(--color-ink)] decoration-[var(--color-rule-strong)] hover:decoration-solid',
      ].join(' ')}
    >
      {fmtNumber(n)}
    </button>
  );
}

/* ---------- Positions · {portal}: kinds × grains ---------- */

export function PositionsGrid({
  portal,
  grid,
  chosen,
  onOpen,
}: {
  portal: string;
  grid: PositionGrid | undefined;
  chosen: PositionCell | null;
  onOpen: (cell: PositionCell) => void;
}) {
  const title = `Positions · ${portalShort(portal)}`;
  if (!grid || Object.keys(grid).length === 0) {
    return (
      <Panel title={title}>
        <p className="text-sm text-[var(--color-ink-3)]">The gauge has stored no positions for this portal.</p>
      </Panel>
    );
  }
  const kinds = gridKinds(grid);
  const grains = gridGrains(grid);
  const total = kinds.reduce((a, k) => a + kindTotal(grid, k), 0);
  /* The list holds what the hourly refresh classified: `unclassified` opens nothing. */
  const listable = (kind: string) => kind !== UNCLASSIFIED;
  return (
    <Panel
      title={title}
      count={fmtNumber(total)}
      note="active ads first written more than 24 h before the reading"
      lede="Where each ad’s point came from, by the grain of its answer. Below address grain a portal pin keeps its pin and reads approximate (ruling Q3). A number opens its ads."
    >
      <div className="overflow-x-auto">
        <table className="w-full border-collapse text-sm">
          <caption className="sr-only">{title}: point kinds by grain; a number opens its ads</caption>
          <thead>
            <tr className="text-left align-bottom">
              <th scope="col" className={`py-1.5 pr-3 ${HEAD}`}>Kind</th>
              {grains.map((g) => {
                const head = POSITION_GRAINS.find((x) => x.key === g);
                return (
                  <th key={g} scope="col" className="py-1.5 pr-3 text-right font-normal">
                    <span className={`block ${HEAD}`}>{grainLabel(g)}</span>
                    <span className="block whitespace-nowrap text-[0.68rem] text-[var(--color-ink-3)]">
                      {precisionWords(head?.rank ?? null)}
                    </span>
                  </th>
                );
              })}
              <th scope="col" className={`py-1.5 text-right ${HEAD}`}>Total</th>
            </tr>
          </thead>
          <tbody>
            {kinds.map((k) => (
              <tr key={k} className={ROW}>
                <th scope="row" title={KIND_LABEL[k]?.title} className="py-1.5 pr-3 text-left font-normal whitespace-nowrap text-[var(--color-ink-2)]">
                  {kindLabel(k)}
                </th>
                {grains.map((g) => (
                  <td key={g} className="py-1.5 pr-3 text-right">
                    <Count
                      n={grid[k]?.[g] ?? 0}
                      name={`${kindLabel(k)} · ${grainLabel(g)}`}
                      cell={listable(k) ? { kind: k, grain: g } : null}
                      chosen={chosen}
                      onOpen={onOpen}
                    />
                  </td>
                ))}
                <td className="py-1.5 text-right">
                  <Count
                    n={kindTotal(grid, k)}
                    name={`${kindLabel(k)} · every grain`}
                    cell={listable(k) ? { kind: k, grain: null } : null}
                    chosen={chosen}
                    onOpen={onOpen}
                  />
                </td>
              </tr>
            ))}
          </tbody>
          <tfoot>
            <tr className="border-t border-[var(--color-rule)]">
              <th scope="row" className="py-1.5 pr-3 text-left font-medium text-[var(--color-ink)]">Every kind</th>
              {grains.map((g) => (
                <td key={g} className="py-1.5 pr-3 text-right">
                  <Count
                    n={grainTotal(grid, g)}
                    name={`Every kind · ${grainLabel(g)}`}
                    cell={{ kind: ALL_KINDS, grain: g }}
                    chosen={chosen}
                    onOpen={onOpen}
                  />
                </td>
              ))}
              <td className="py-1.5 text-right">
                <Count n={total} name="Every kind · every grain" cell={{ kind: ALL_KINDS, grain: null }} chosen={chosen} onOpen={onOpen} />
              </td>
            </tr>
          </tfoot>
        </table>
      </div>
    </Panel>
  );
}

/* ---------- By kind: portals × kinds, summed over grain ---------- */

export function KindStrip({
  grids,
  portal,
  chosen,
  onOpen,
}: {
  grids: ReadonlyArray<readonly [string, PositionGrid]>;
  portal: string;
  chosen: PositionCell | null;
  onOpen: (portal: string, cell: PositionCell) => void;
}) {
  const held = grids.filter(([, g]) => Object.keys(g).length > 0);
  if (held.length === 0) return null;
  const kinds = gridKinds(Object.assign({}, ...held.map(([, g]) => g)) as PositionGrid);
  return (
    <Panel title="By kind" note="each portal’s positions summed over grain">
      <div className="overflow-x-auto">
        <table className="w-full border-collapse text-sm">
          <caption className="sr-only">By kind: portals by point kind; a number opens its ads</caption>
          <thead>
            <tr className="text-left align-bottom">
              <th scope="col" className={`py-1.5 pr-3 ${HEAD}`}>Portal</th>
              {kinds.map((k) => (
                <th key={k} scope="col" title={KIND_LABEL[k]?.title} className={`py-1.5 pr-3 text-right ${HEAD}`}>
                  {kindLabel(k)}
                </th>
              ))}
              <th scope="col" className={`py-1.5 text-right ${HEAD}`}>Total</th>
            </tr>
          </thead>
          <tbody>
            {held.map(([p, grid]) => (
              <tr key={p} className={ROW}>
                <th scope="row" className="py-1.5 pr-3 text-left font-normal text-[var(--color-ink-2)]">{portalShort(p)}</th>
                {kinds.map((k) => (
                  <td key={k} className="py-1.5 pr-3 text-right">
                    <Count
                      n={kindTotal(grid, k)}
                      name={`${portalShort(p)} · ${kindLabel(k)}`}
                      cell={k === UNCLASSIFIED ? null : { kind: k, grain: null }}
                      chosen={p === portal ? chosen : null}
                      onOpen={(cell) => onOpen(p, cell)}
                    />
                  </td>
                ))}
                <td className="py-1.5 text-right font-mono tabular-nums text-[0.78rem] text-[var(--color-ink)]">
                  {fmtNumber(kinds.reduce((a, k) => a + kindTotal(grid, k), 0))}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </Panel>
  );
}

/* ---------- Ads: the cell's list ---------- */

const SORT_OPTIONS = [
  { field: 'last_seen_at' as const, label: 'last seen' },
  { field: 'first_seen_at' as const, label: 'first seen' },
];

const STATUS: ReadonlyArray<{ value: PositionListFilters['status']; label: string }> = [
  { value: 'all', label: 'all' },
  { value: 'active', label: 'active' },
  { value: 'delisted', label: 'delisted' },
];
const EVIDENCE: ReadonlyArray<{ value: PositionListFilters['evidence']; label: string; title: string }> = [
  { value: 'all', label: 'all', title: 'Every ad with no position' },
  { value: 'unresolved', label: 'unresolved', title: 'Holds a town-bearing claim and no town (rule 25: Problem)' },
  { value: 'withheld', label: 'withheld', title: 'Holds no town-bearing claim: the page names no town (Watch)' },
];
const SIBLING: ReadonlyArray<{ value: PositionListFilters['sibling']; label: string }> = [
  { value: 'all', label: 'all' },
  { value: 'yes', label: 'with a point' },
  { value: 'no', label: 'without' },
];

/* A row's precision words where it HAS a point; an ad abroad or with no position has none to qualify. */
const rowPrecision = (r: PositionAuditRow): string =>
  r.position_kind === 'none' || r.position_kind === 'foreign' ? 'no point' : precisionWords(r.granularity_rank);

export function PositionList({
  portal,
  cell,
  onClose,
}: {
  portal: string;
  cell: PositionCell;
  onClose: () => void;
}) {
  const [filters, setFilters] = useState<PositionListFilters>(() => listFilters(portal, cell));
  const [sort, setSort] = useState<SortSpec>({ field: 'last_seen_at', direction: 'desc' });
  const list = useInfiniteList<PositionAuditRow, PositionAuditPage>({
    queryKey: ['position-audit', 'list', JSON.stringify(filters), sort.field, sort.direction],
    queryFn: (cursor, signal) => fetchPositionAuditPage(filters, sort, cursor as KeysetCursor | null, { signal }),
    pageSize: POSITION_AUDIT_PAGE_SIZE,
    getRowId: (r) => r.listing_id,
  });
  const total = list.firstPage?.total ?? null;
  const refreshedAt = list.rows[0]?.refreshed_at ?? null;
  const none = cell.kind === 'none';
  const everyStatus = none || cell.kind === ALL_KINDS;
  const what = `${portalShort(portal)} · ${cell.kind === ALL_KINDS ? 'every kind' : kindLabel(cell.kind)} · ${grainLabel(cell.grain)}`;

  return (
    <Panel
      title="Ads"
      note={what}
      accessory={
        <button
          type="button"
          onClick={onClose}
          className="text-xs text-[var(--color-ink-3)] underline decoration-dotted underline-offset-2 hover:text-[var(--color-ink)]"
        >
          Close
        </button>
      }
      lede={`One row per ad of the cell, from the hourly snapshot${refreshedAt ? ` of ${fmtDateSlash(refreshedAt)}` : ''}: active ads of any age${everyStatus ? ', and every ad in any status with no position' : ''}. The grid counts the gauge’s own reading, so the two can differ by the ads in between.`}
    >
      {list.error ? <ErrorBanner message={list.error.message} /> : null}
      <div className="flex flex-wrap items-center gap-x-4 gap-y-2 text-[0.78rem] text-[var(--color-ink-3)]">
        {total != null ? (
          <span data-testid="position-total" className="font-mono tabular-nums text-[var(--color-ink)]">
            {fmtNumber(total)} ads
          </span>
        ) : null}
        {/* A click on the lit order flips its direction; the other order starts newest first. */}
        <SegmentedBar
          hug
          label="Sort by"
          options={SORT_OPTIONS.map((o) => ({
            value: o.field,
            label: sort.field === o.field ? `${o.label} ${sort.direction === 'desc' ? '↓' : '↑'}` : o.label,
          }))}
          value={sort.field}
          onChange={(field) =>
            setSort((s) =>
              s.field === field ? { ...s, direction: s.direction === 'desc' ? 'asc' : 'desc' } : { field, direction: 'desc' },
            )
          }
        />
        {everyStatus ? (
          <SegmentedBar hug label="Status" options={STATUS} value={filters.status}
            onChange={(status) => setFilters((f) => ({ ...f, status }))} />
        ) : null}
        {none ? (
          <>
            <SegmentedBar hug label="Town evidence" options={EVIDENCE} value={filters.evidence}
              onChange={(evidence) => setFilters((f) => ({ ...f, evidence }))} />
            <SegmentedBar hug label="Sibling" options={SIBLING} value={filters.sibling}
              onChange={(sibling) => setFilters((f) => ({ ...f, sibling }))} />
          </>
        ) : null}
      </div>
      <div className="overflow-x-auto">
        <table className="min-w-full text-[0.8rem]">
          <caption className="sr-only">Ads of {what}</caption>
          <thead>
            <tr className="text-left">
              <th scope="col" className={`py-1.5 pr-3 ${HEAD}`}>Ad</th>
              <th scope="col" className={`py-1.5 pr-3 ${HEAD}`}>Kind</th>
              <th scope="col" className={`py-1.5 pr-3 ${HEAD}`}>Grain</th>
              <th scope="col" className={`py-1.5 pr-3 ${HEAD}`}>Status</th>
              <th scope="col" className={`py-1.5 pr-3 text-right ${HEAD}`}>Last seen</th>
              {none ? (
                <>
                  <th scope="col" className={`py-1.5 pr-3 ${HEAD}`}>Town evidence</th>
                  <th scope="col" className={`py-1.5 pr-3 ${HEAD}`} title="Another ad of the same property has a point">Sibling</th>
                  <th scope="col" className={`py-1.5 pr-3 ${HEAD}`} title="A re-resolve is queued">Queue</th>
                </>
              ) : null}
            </tr>
          </thead>
          <tbody>
            {list.rows.map((r) => {
              const name = [categoryMainLabel(r.category_main), r.disposition, r.area_m2 != null ? fmtArea(r.area_m2) : null]
                .filter(Boolean)
                .join(' · ');
              return (
                <tr key={r.listing_id} className={`${ROW} align-top`} data-testid={`position-row-${r.listing_id}`}>
                  <td className="py-1.5 pr-3">
                    {r.property_id != null ? (
                      <Link to={propertyPath(r.property_id, r.listing_id)} className="text-[var(--color-copper)] hover:underline">
                        {name}
                      </Link>
                    ) : (
                      <span className="text-[var(--color-ink)]">{name}</span>
                    )}
                    <span className="block text-[var(--color-ink-3)]">{r.display_label ?? '—'}</span>
                    {r.source_url ? (
                      <a
                        href={r.source_url}
                        target="_blank"
                        rel="noreferrer"
                        className="text-[0.72rem] text-[var(--color-ink-3)] underline decoration-dotted underline-offset-2"
                      >
                        portal ↗
                      </a>
                    ) : null}
                  </td>
                  <td className="py-1.5 pr-3 whitespace-nowrap">{kindLabel(r.position_kind)}</td>
                  <td className="py-1.5 pr-3 whitespace-nowrap">
                    {grainLabel(r.grain)}
                    <span className="block text-[var(--color-ink-3)]">{rowPrecision(r)}</span>
                  </td>
                  <td className="py-1.5 pr-3">{r.is_active ? 'active' : 'delisted'}</td>
                  <td className="py-1.5 pr-3 text-right font-mono tabular-nums">{fmtDateSlash(r.last_seen_at)}</td>
                  {none ? (
                    <>
                      <td className="py-1.5 pr-3">{r.town_evidence == null ? '—' : r.town_evidence ? 'unresolved' : 'withheld'}</td>
                      <td className="py-1.5 pr-3">{r.sibling_placed ? 'has a point' : '—'}</td>
                      <td className="py-1.5 pr-3">{r.queued ? 'queued' : '—'}</td>
                    </>
                  ) : null}
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
      />
    </Panel>
  );
}

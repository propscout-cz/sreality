/* Browse › Makléři (docs/design/broker-board.md): the Browse cohort counted by
 * broker. The sidebar, pills and presets are Browse's; this tab owns its options
 * (lib/brokerBoard, in the URL) and its rows. Its own chunk, like Statistiky. */

import { useEffect, useRef, useState } from 'react';
import { Link } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';

import ErrorBanner from '@/components/ErrorBanner';
import SettingsRow from '@/components/SettingsRow';
import Skeleton from '@/components/Skeleton';
import { PickButton, Segmented } from '@/components/controls';
import { listBrokerMergeCandidates } from '@/lib/api';
import { useAuth } from '@/lib/auth';
import {
  BOARD_HORIZONS,
  BOARD_LIMITS,
  shownSort,
  speedOf,
  useBrokerBoard,
  type BoardMeta,
  type BoardOptions,
  type BoardRow,
  type BoardSort,
  type BrokerBoardResult,
} from '@/lib/brokerBoard';
import {
  contactState,
  prettyPhone,
  searchBrokerFirms,
  searchBrokersByName,
  type BrokerFirmOption,
} from '@/lib/brokers';
import type { ListingFilters } from '@/lib/filters';
import { czPlural, fmtCount } from '@/lib/format';
import { ROUTES } from '@/lib/routes';
import { isSupabaseConfigured } from '@/lib/supabase';
import { useDebouncedTerm } from '@/lib/useDebouncedTerm';

import { CoverageLine, IgnoredNote, MarketLine, RefusalLine, SpeedLegend, sharePct } from './BoardNotes';

const HORIZON_OPTIONS = BOARD_HORIZONS.map((h) => ({ value: h, label: String(h) }));
const MIN_OPTIONS = [1, 3, 5, 10, 25].map((n) => ({ value: n, label: String(n) }));
const LIMIT_OPTIONS = BOARD_LIMITS.map((n) => ({ value: n, label: String(n) }));
const FIRM_PILLS_SHOWN = 5;
const LINE = 'text-[0.75rem] leading-snug text-[var(--color-ink-3)]';

export default function BrokerBoard({
  filters,
  board,
  onBoardChange,
}: {
  filters: ListingFilters;
  board: BoardOptions;
  onBoardChange: (patch: Partial<BoardOptions>) => void;
}) {
  const boardQ = useBrokerBoard(filters, board);

  return (
    <div className="space-y-4">
      <section className="@container rounded-[var(--radius-md)] border border-[var(--color-rule)] bg-[var(--color-paper-2)] px-5">
        <div className="divide-y divide-[var(--color-rule-soft)]">
          <SettingsRow label="Makléř">
            <div className="flex flex-wrap items-center gap-3">
              <NameSearch />
              <ReviewLink />
            </div>
          </SettingsRow>
          <SettingsRow label="Výpis">
            <div className={`flex flex-wrap items-center gap-x-6 gap-y-2 ${LINE}`}>
              <span className="inline-flex items-center gap-2">
                Opustilo trh do
                <Segmented
                  label="Opustilo trh do (dní)"
                  options={HORIZON_OPTIONS}
                  value={board.horizonDays}
                  onChange={(horizonDays) => onBoardChange({ horizonDays })}
                />
                dní
              </span>
              <span className="inline-flex items-center gap-2">
                Aspoň
                <Segmented
                  label="Aspoň nemovitostí"
                  options={MIN_OPTIONS}
                  value={board.minProperties}
                  onChange={(minProperties) => onBoardChange({ minProperties })}
                />
                nemovitostí
              </span>
              <span className="inline-flex items-center gap-2">
                Počet
                <Segmented
                  label="Počet"
                  options={LIMIT_OPTIONS}
                  value={board.limit}
                  onChange={(limit) => onBoardChange({ limit })}
                />
              </span>
            </div>
          </SettingsRow>
          <SettingsRow label="Firma">
            <CompanyFilter value={board.firmIds} onChange={(firmIds) => onBoardChange({ firmIds })} />
          </SettingsRow>
        </div>
      </section>

      <IgnoredNote filters={filters} className={LINE} />

      {boardQ.isPending ? (
        <Skeleton height={320} />
      ) : boardQ.isError ? (
        <ErrorBanner
          title="Žebříček se nepodařilo načíst:"
          message={boardQ.error.message}
          onRetry={() => void boardQ.refetch()}
        />
      ) : (
        <Ledger
          result={boardQ.data}
          filters={filters}
          board={board}
          stale={boardQ.isPlaceholderData}
          onSort={(sort) => onBoardChange({ sort })}
        />
      )}
    </div>
  );
}

/* /broker-review/candidates is require_admin while this tab is only agenda-
 * gated, so an ungated query is two guaranteed 403s per non-admin view (retry:
 * 1, and react-query refetches an errored query on every remount), each one
 * opening a Postgres connection before the auth check runs. reason_counts is
 * the whole queue, so one row is asked for. The `!isSupabaseConfigured()` arm
 * mirrors Shell's `showAdmin` and guards.tsx for local dev. */
function ReviewLink() {
  const { isAdmin } = useAuth();
  const reviewQ = useQuery({
    queryKey: ['broker-merge-candidates-count'],
    queryFn: () => listBrokerMergeCandidates(1),
    staleTime: 300_000,
    enabled: isAdmin || !isSupabaseConfigured(),
  });
  const reviewCount = Object.values(reviewQ.data?.reason_counts ?? {}).reduce((a, b) => a + b, 0);
  if (reviewCount === 0) return null;
  return (
    <Link
      to={ROUTES.brokersReview.build()}
      className="ml-auto shrink-0 text-xs rounded-[var(--radius-sm)] border border-[var(--color-copper)] bg-[var(--color-copper-soft)] px-3 py-1.5 text-[var(--color-copper)] hover:bg-[var(--color-copper)] hover:text-[var(--color-on-copper)] transition-colors"
    >
      Sloučit duplicity ({reviewCount})
    </Link>
  );
}

function NameSearch() {
  const [q, debounced, setQ] = useDebouncedTerm();
  const [open, setOpen] = useState(false);

  const resultsQ = useQuery({
    queryKey: ['broker-name-search', debounced],
    queryFn: () => searchBrokersByName(debounced),
    enabled: debounced.length >= 2,
    staleTime: 60_000,
  });
  const results = resultsQ.data ?? [];

  return (
    <div className="relative min-w-0 flex-1 max-w-xl">
      <input
        value={q}
        onChange={(e) => {
          setQ(e.target.value);
          setOpen(true);
        }}
        onFocus={() => setOpen(true)}
        onBlur={() => setTimeout(() => setOpen(false), 150)}
        aria-label="Hledat makléře podle jména"
        placeholder="Hledat makléře podle jména…"
        className="w-full text-sm border border-[var(--color-rule)] rounded-[var(--radius-sm)] bg-[var(--color-inset)] px-3 py-1.5 text-[var(--color-ink)] placeholder:text-[var(--color-ink-4)] focus-visible:ring-2 focus-visible:ring-[var(--color-focus)]"
      />
      {open && debounced.length >= 2 && (
        <div className="absolute z-20 mt-1 w-full border border-[var(--color-rule)] rounded-[var(--radius-md)] bg-[var(--color-paper-3)] shadow-sm max-h-80 overflow-y-auto">
          {resultsQ.isLoading ? (
            <p className="px-3 py-2 text-sm text-[var(--color-ink-3)]">Hledám…</p>
          ) : resultsQ.isError ? (
            /* A failed search is not an empty one — "Nic nenalezeno." for both is
               exactly how the dark PostgREST reads stayed invisible for a month. */
            <p className="px-3 py-2 text-sm text-[var(--color-brick)]">
              Hledání selhalo: {(resultsQ.error as Error).message}
            </p>
          ) : results.length === 0 ? (
            <p className="px-3 py-2 text-sm text-[var(--color-ink-4)]">Nic nenalezeno.</p>
          ) : (
            results.map((b) => (
              /* onMouseDown-preventDefault keeps the input's onBlur from
                 closing the list before the click lands; it does NOT suppress
                 the anchor's own navigation, which fires on click. */
              <Link
                key={b.broker_id}
                to={ROUTES.brokerDetail.build({ id: b.broker_id })}
                onMouseDown={(e) => e.preventDefault()}
                className="w-full text-left px-3 py-2 flex items-center justify-between gap-3 border-b border-[var(--color-rule-soft)] last:border-0 hover:bg-[var(--color-copper-soft)]"
              >
                <span className="min-w-0">
                  <span className="block truncate text-sm text-[var(--color-ink)]">
                    {b.display_name ?? 'Neznámý makléř'}
                  </span>
                  <span className="block truncate text-xs text-[var(--color-ink-3)]">
                    {b.firm_name ?? b.firm_domain ?? 'nezávislý'}
                  </span>
                </span>
                {/* The CZ-scoped count is what the list is ordered by (migration
                    396), so it has to be the number shown; the whole book stays
                    visible as the dimmed total whenever the broker also carries
                    foreign or ungeocoded stock. */}
                <span className="shrink-0 text-xs font-[family-name:var(--font-mono)] tabular-nums text-[var(--color-ink-3)]">
                  {fmtCount(b.cz_active_property_count)}
                  {b.active_property_count > b.cz_active_property_count && (
                    <span className="text-[var(--color-ink-4)]"> / {fmtCount(b.active_property_count)}</span>
                  )}
                </span>
              </Link>
            ))
          )}
        </div>
      )}
    </div>
  );
}

// display_name is NULL for every franchise domain (mmreality.cz, re-max.cz, ...)
// and any domain under the resolver's 60% modal-label share — the same fallback
// the board row itself uses for firm_name ?? firm_domain.
function firmLabel(f: BrokerFirmOption): string {
  return f.display_name ?? f.canonical_domain ?? 'neznámá firma';
}

// The busiest companies as toggleable pills, multi-select — searchBrokerFirms('')
// is the "browse top firms by broker_count" path.
function CompanyFilter({
  value,
  onChange,
}: {
  value: number[];
  onChange: (next: number[]) => void;
}) {
  const optionsQ = useQuery({
    queryKey: ['broker-firm-options', ''],
    queryFn: () => searchBrokerFirms('', 24),
    staleTime: 60_000,
  });
  const options = optionsQ.data ?? [];
  const selected = new Set(value);
  const [open, setOpen] = useState(false);
  const pillsRef = useRef<HTMLDivElement>(null);
  const focusAt = useRef<number | null>(null);

  // The opener leaves the DOM as it opens, so focus moves to the first pill it
  // revealed rather than dropping to the page top.
  useEffect(() => {
    if (focusAt.current == null) return;
    (pillsRef.current?.children[focusAt.current] as HTMLElement | undefined)?.focus();
    focusAt.current = null;
  }, [open]);

  const toggle = (firmId: number) => {
    if (selected.has(firmId)) onChange(value.filter((id) => id !== firmId));
    else onChange([...value, firmId]);
  };

  if (optionsQ.isLoading) {
    return <p className="text-sm text-[var(--color-ink-3)]">Načítám firmy…</p>;
  }
  if (optionsQ.isError) {
    return <p className="text-sm text-[var(--color-brick)]">Firmy se nepodařilo načíst.</p>;
  }

  // P29: the five busiest (the server sends them by broker_count), then "+ N
  // dalších". A chosen firm stays in view: a refetch can re-rank it past the
  // fifth, and a pressed pill must never be hidden.
  const inView = (f: BrokerFirmOption, i: number) =>
    open || i < FIRM_PILLS_SHOWN || selected.has(f.firm_id);
  const shown = options.filter(inView);
  const more = options.length - shown.length;

  return (
    <div ref={pillsRef} className="flex flex-wrap gap-1">
      {shown.map((f) => (
        <PickButton key={f.firm_id} on={selected.has(f.firm_id)} onClick={() => toggle(f.firm_id)}>
          {firmLabel(f)}{' '}
          <span className="font-[family-name:var(--font-mono)] tabular-nums opacity-70">
            ({fmtCount(f.broker_count)})
          </span>
        </PickButton>
      ))}
      {more > 0 && (
        <button
          type="button"
          onClick={() => {
            focusAt.current = options.findIndex((f, i) => !inView(f, i));
            setOpen(true);
          }}
          className="px-2 py-1.5 text-xs rounded-[var(--radius-sm)] border border-transparent text-[var(--color-ink-2)] hover:text-[var(--color-ink)] hover:border-[var(--color-rule)] transition-colors"
        >
          +{' '}
          <span className="font-[family-name:var(--font-mono)] tabular-nums">{fmtCount(more)}</span>{' '}
          {czPlural(more, 'další', 'další', 'dalších')}
        </button>
      )}
    </div>
  );
}

/* No padding or alignment here: each cell states its own, so no two utilities compete. */
const HEAD = 'py-2 text-[0.6rem] font-medium tracking-[0.14em] uppercase text-[var(--color-ink-3)]';
const CELL = 'py-2.5 align-baseline';

function Ledger({
  result: { rows, meta },
  filters,
  board,
  stale,
  onSort,
}: {
  result: BrokerBoardResult;
  filters: ListingFilters;
  board: BoardOptions;
  /* The previous filter's rows, kept on screen while the next answer loads. */
  stale: boolean;
  onSort: (sort: BoardSort) => void;
}) {
  const refused = meta.speed_refused != null;
  const sort = shownSort(board, meta);
  const days = meta.horizon_days;
  return (
    <section
      aria-label="Žebříček makléřů"
      aria-busy={stale || undefined}
      className={`rounded-[var(--radius-md)] border border-[var(--color-rule)] transition-opacity${stale ? ' opacity-60' : ''}`}
    >
      <div className="space-y-1 border-b border-[var(--color-rule)] bg-[var(--color-paper-2)] px-4 py-2.5">
        <div className="flex flex-wrap items-baseline gap-x-4 gap-y-1">
          <CoverageLine meta={meta} filters={filters} className={`min-w-0 flex-1 ${LINE}`} />
          <span className={`shrink-0 ${LINE}`}>
            <span className="font-mono tabular-nums">{fmtCount(rows.length)}</span>{' '}
            {czPlural(rows.length, 'makléř', 'makléři', 'makléřů')}
            {rows.length >= board.limit ? ' (limit)' : ''}
          </span>
        </div>
        <MarketLine meta={meta} className={LINE} />
        <RefusalLine meta={meta} className="text-sm text-[var(--color-ink-2)]" />
      </div>

      {rows.length === 0 ? (
        <div className="px-4 py-8 text-center">
          <p className="text-sm text-[var(--color-ink-2)]">Žádní makléři pro tento výběr.</p>
          <p className={`mt-1 ${LINE}`}>Zkuste širší filtry, jinou firmu nebo nižší minimum nemovitostí.</p>
        </div>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full border-collapse text-left">
            <thead>
              <tr>
                <th scope="col" className={`${HEAD} w-10 pl-4 pr-2 text-right`}>
                  <span className="sr-only">Pořadí</span>
                </th>
                <th scope="col" className={`${HEAD} px-2 text-left`}>Makléř</th>
                <th scope="col" className={`${HEAD} px-2 text-left`}>Firma</th>
                <th scope="col" className={`${HEAD} hidden px-2 text-left sm:table-cell`}>Telefon</th>
                <SortHead label="Nemovitostí" by="properties" sort={sort} onSort={onSort} />
                <SortHead
                  label={`Opustilo trh do ${days} dní`}
                  by="speed"
                  sort={sort}
                  onSort={onSort}
                  disabled={refused}
                  title="prodáno nebo staženo, ne nutně prodej"
                />
                <th
                  scope="col"
                  className={`${HEAD} hidden pl-2 pr-4 text-right md:table-cell`}
                  title="Vedeno jako aktivní, ale dlouho neviděno; do n se nepočítá."
                >
                  Nezjištěno
                </th>
              </tr>
            </thead>
            <tbody>
              {rows.map((r, i) => (
                <tr
                  key={r.broker_id}
                  className="border-t border-[var(--color-rule-soft)] hover:bg-[var(--color-copper-soft)] transition-colors"
                >
                  <td className={`${CELL} pl-4 pr-2 text-right font-[family-name:var(--font-display)] text-lg tabular-nums text-[var(--color-copper)]`}>
                    {i + 1}
                  </td>
                  <td className={`${CELL} min-w-[11rem] px-2`}>
                    {/* A real anchor: right-click → open in a new tab, ctrl/middle-
                      * click and a copyable address. The board's state is in the
                      * URL, so a plain click navigates here and Back returns. */}
                    <Link
                      to={ROUTES.brokerDetail.build({ id: r.broker_id })}
                      className="font-[family-name:var(--font-display)] text-[0.98rem] text-[var(--color-ink)] hover:text-[var(--color-copper-2)]"
                    >
                      {r.display_name ?? 'Neznámý makléř'}
                    </Link>
                  </td>
                  <td className={`${CELL} px-2 text-xs text-[var(--color-ink-3)]`}>
                    {r.firm_name ?? r.firm_domain ?? 'nezávislý / neznámá kancelář'}
                  </td>
                  <td className={`${CELL} hidden px-2 sm:table-cell`}>
                    <PhoneCell row={r} />
                  </td>
                  <td className={`${CELL} px-2 text-right`}>
                    {/* The unit is the column head's; each figure keeps it for screen readers only (P28). */}
                    <span
                      className={`font-[family-name:var(--font-mono)] tabular-nums ${
                        sort === 'properties' ? 'text-base text-[var(--color-ink)]' : 'text-sm text-[var(--color-ink-3)]'
                      }`}
                    >
                      {fmtCount(r.properties)}
                      <span className="sr-only"> nemovitostí</span>
                    </span>
                  </td>
                  <td className={`${CELL} px-2 text-right`}>
                    <SpeedCell row={r} meta={meta} emphasized={sort === 'speed'} />
                  </td>
                  <td className={`${CELL} hidden pl-2 pr-4 text-right font-mono text-sm tabular-nums text-[var(--color-ink-3)] md:table-cell`}>
                    {fmtCount(r.unknown)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      <SpeedLegend
        meta={meta}
        className="border-t border-[var(--color-rule-soft)] px-4 py-2.5 text-[0.7rem] leading-relaxed text-[var(--color-ink-3)]"
      />
    </section>
  );
}

/* A sortable column head: the server orders the rows (volume, or the Wilson
 * lower bound), always descending. Colour only marks the chosen column, and the
 * arrow keeps its room, so no label shifts. */
function SortHead({
  label,
  by,
  sort,
  onSort,
  disabled = false,
  title,
}: {
  label: string;
  by: BoardSort;
  sort: BoardSort;
  onSort: (sort: BoardSort) => void;
  disabled?: boolean;
  title?: string;
}) {
  const on = sort === by;
  return (
    <th scope="col" aria-sort={on ? 'descending' : 'none'} className={`${HEAD} whitespace-nowrap px-2 text-right`}>
      <button
        type="button"
        onClick={() => onSort(by)}
        disabled={disabled}
        title={title}
        className={`inline-flex items-baseline gap-1 uppercase tracking-[0.14em] transition-colors disabled:cursor-not-allowed disabled:opacity-50 ${
          on ? 'text-[var(--color-ink)]' : 'text-[var(--color-ink-3)] hover:text-[var(--color-ink-2)]'
        }`}
      >
        {label}
        <span aria-hidden className={on ? undefined : 'invisible'}>
          ↓
        </span>
      </button>
    </th>
  );
}

/* The API hands a non-admin `has_phone` instead of the number itself, so an
   em-dash here would read as "this broker has no phone on file". */
function PhoneCell({ row }: { row: BoardRow }) {
  const phone = contactState(row.primary_phone, row.has_phone);
  return (
    <span
      className="block w-40 text-xs font-[family-name:var(--font-mono)] text-[var(--color-ink-2)] tabular-nums"
      title={phone.state === 'masked' ? 'Kontakt je viditelný jen pro administrátory.' : undefined}
    >
      {phone.state === 'value' ? (
        prettyPhone(phone.value)
      ) : phone.state === 'masked' ? (
        <span className="text-[var(--color-ink-4)]">na vyžádání</span>
      ) : (
        '—'
      )}
    </span>
  );
}

/* k z n and the share, read against the market: the bar fills to the share and
 * the copper tick stands at the cohort's own share (MarketLine). Under min_n
 * only n is shown; refused or never followed, the cell is absent. */
function SpeedCell({ row, meta, emphasized }: { row: BoardRow; meta: BoardMeta; emphasized: boolean }) {
  const s = speedOf(row, meta);
  if (s.kind === 'refused' || (s.kind === 'small' && s.n === 0)) {
    return <span className="text-[var(--color-ink-4)]">—</span>;
  }
  if (s.kind === 'small') {
    return (
      <span
        className="font-mono text-xs tabular-nums text-[var(--color-ink-3)]"
        title={`Méně než ${meta.min_n} sledovaných: podíl se neukazuje.`}
      >
        n = {fmtCount(s.n)}
      </span>
    );
  }
  const market = meta.market.share;
  return (
    <span
      className="inline-flex items-center gap-2 whitespace-nowrap"
      title={s.rank == null ? undefined : `Dolní mez (Wilson, 95 %): ${sharePct(s.rank)}`}
    >
      <span className="font-mono text-xs tabular-nums text-[var(--color-ink-3)]">
        {fmtCount(s.exited)} z {fmtCount(s.n)}
      </span>
      <span
        className={`w-10 text-right font-mono tabular-nums ${
          emphasized ? 'text-base text-[var(--color-ink)]' : 'text-sm text-[var(--color-ink-2)]'
        }`}
      >
        {sharePct(s.share)}
      </span>
      <span aria-hidden className="relative h-[3px] w-12 rounded-full bg-[var(--color-rule)]">
        <span
          className="absolute inset-y-0 left-0 rounded-full bg-[var(--color-ink-3)]"
          style={{ width: `${s.share * 100}%` }}
        />
        {market != null && (
          <span
            className="absolute -top-[3px] h-[9px] w-px bg-[var(--color-copper)]"
            style={{ left: `${market * 100}%` }}
          />
        )}
      </span>
    </span>
  );
}
